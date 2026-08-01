"""Cross-package tests for fail-closed execution and benchmark controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.context.assembler import select_repository_excerpts
from product_factory.domain.errors import RuntimeFailureError
from product_factory.domain.runs import RunRequest
from product_factory.evaluation.bench import BenchmarkRunner
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalToolCall,
    ModelRequest,
    ModelResponse,
)
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator, default_code_change_plan


class EmptyLiveGateway(ModelGateway):
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            provider="test",
            provider_model_id="test",
            resolved_model_id="test",
            status="success",
            text="",
        )

    def refresh_catalog(self) -> dict:
        return {"models": []}

    def list_models(self) -> list[dict]:
        return []


class ScriptedLiveGateway(EmptyLiveGateway):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def test_live_empty_model_output_fails_without_fallback(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=EmptyLiveGateway(),
        data_dir=tmp_path / ".product-factory",
        # Isolate worker behavior while avoiding a planner model dependency.
        use_deterministic_planner=True,
    )
    with pytest.raises(RuntimeFailureError, match="empty_model_output"):
        coordinator.run(
            RunRequest(
                request_id="fail-closed",
                workflow_type="code_change",
                request_text="Add a cache helper",
                repository_path=_git_repo(tmp_path / "repo"),
                approval_policy="none",
            )
        )
    assert not list((tmp_path / ".product-factory" / "runs").glob("*/output/proposed.patch"))


def test_context_selection_is_relevant_bounded_and_line_numbered(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cache.py").write_text("class Cache:\n    pass\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("other\n", encoding="utf-8")
    (repo / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    excerpts, omissions = select_repository_excerpts(
        repo, objective="Implement cache expiration", max_files=1
    )
    assert excerpts[0]["path"] == "cache.py"
    assert excerpts[0]["content"].startswith("1: class Cache")
    assert all(excerpt["path"] != ".env" for excerpt in excerpts)
    assert omissions


def test_live_review_receives_patch_and_returns_structured_findings(tmp_path: Path) -> None:
    def response(
        status: str,
        *,
        calls: list[CanonicalToolCall] | None = None,
        text: str = "",
        structured: dict | None = None,
    ) -> ModelResponse:
        return ModelResponse(
            request_id="script",
            provider="test",
            provider_model_id="test",
            resolved_model_id="test",
            status=status,  # type: ignore[arg-type]
            text=text,
            structured_data=structured,
            tool_calls=calls or [],
        )

    gateway = ScriptedLiveGateway(
        [
            response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(id="list", name="list_files", arguments={"directory": "."})
                ],
            ),
            response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(id="read", name="read_file", arguments={"path": "service.py"})
                ],
            ),
            response(
                "tool_calls",
                calls=[
                    CanonicalToolCall(
                        id="write",
                        name="create_file",
                        arguments={
                            "path": "src/auth.py",
                            "content": "AUTHORIZED = True\n",
                        },
                    )
                ],
            ),
            response("success", text="implemented"),
            response("success", structured={"findings": []}),
        ]
    )
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=gateway,
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="structured-review",
            workflow_type="code_change",
            request_text="Change authentication permissions safely",
            repository_path=_git_repo(tmp_path / "repo"),
            approval_policy="none",
        )
    )
    run_output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    assert (run_output / "review-findings.json").exists()
    review_request = next(request for request in gateway.requests if request.task_id == "T-003")
    assert "src/auth.py" in review_request.messages[-1].content


def test_repair_inherits_failed_patch_and_composition_uses_repaired_lineage(
    tmp_path: Path,
) -> None:
    def tool_response(call: CanonicalToolCall) -> ModelResponse:
        return ModelResponse(
            request_id="script",
            provider="test",
            provider_model_id="test",
            resolved_model_id="test",
            status="tool_calls",
            tool_calls=[call],
        )

    def finished() -> ModelResponse:
        return ModelResponse(
            request_id="script",
            provider="test",
            provider_model_id="test",
            resolved_model_id="test",
            status="success",
            text="done",
        )

    good_source = (
        '"""Minimal app entrypoint."""\n\n\n'
        "REPAIRED = True\n\n\n"
        "def hello() -> str:\n"
        '    return "hello"\n'
    )
    gateway = ScriptedLiveGateway(
        [
            tool_response(
                CanonicalToolCall(id="i-list", name="list_files", arguments={"directory": "."})
            ),
            tool_response(
                CanonicalToolCall(
                    id="i-read", name="read_file", arguments={"path": "src/app/main.py"}
                )
            ),
            tool_response(
                CanonicalToolCall(
                    id="i-write",
                    name="create_file",
                    arguments={
                        "path": "src/app/main.py",
                        "content": "def hello(:\n",
                        "overwrite": True,
                    },
                )
            ),
            finished(),
            tool_response(
                CanonicalToolCall(id="r-list", name="list_files", arguments={"directory": "."})
            ),
            tool_response(
                CanonicalToolCall(
                    id="r-read", name="read_file", arguments={"path": "src/app/main.py"}
                )
            ),
            tool_response(
                CanonicalToolCall(
                    id="r-write",
                    name="create_file",
                    arguments={
                        "path": "src/app/main.py",
                        "content": good_source,
                        "overwrite": True,
                    },
                )
            ),
            finished(),
        ]
    )
    root = Path(__file__).resolve().parents[2]
    repo = tmp_path / "repo"
    from tests.conftest import clone_fixture

    clone_fixture(root / "tests/fixtures/sample_api", repo)
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=gateway,
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="repair-lineage",
            workflow_type="code_change",
            request_text="Update the greeting implementation",
            repository_path=repo,
            approval_policy="none",
            metadata={"smoke_commands": "python_tests"},
        )
    )
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    patch = (output / "proposed.patch").read_text(encoding="utf-8")
    assert "REPAIRED" in patch
    repair_lineage = list(output.glob("R-*-lineage.json"))
    assert repair_lineage
    assert manifest.final_status == "completed"


def test_planner_removes_low_value_roles_but_keeps_high_risk_review() -> None:
    low = default_code_change_plan("Add a cache helper")
    high = default_code_change_plan("Change authentication and database permissions")
    assert [task.capability for task in low.tasks] == [
        "implementation",
        "composition",
        "composition",
    ]
    assert "independent_review" in [task.capability for task in high.tasks]


def test_three_seed_execution_and_resume(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    runner = BenchmarkRunner(
        app_config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    report = runner.run(
        cases_dir=root / "tests" / "eval_cases",
        subjects=["single_agent_baseline"],
        limit=1,
        seeds=3,
        case_ids=["code_cache"],
    )
    assert len(report.scores) == 3
    assert {score.seed for score in report.scores} == {0, 1, 2}
    resumed = runner.run(
        cases_dir=root / "tests" / "eval_cases",
        subjects=["single_agent_baseline"],
        limit=1,
        seeds=3,
        case_ids=["code_cache"],
        resume_bench_id=report.bench_id,
    )
    assert len(resumed.scores) == 3


def test_writer_path_conflicts_are_detected() -> None:
    from product_factory.repositories.patches import (
        changed_paths_from_patch,
        detect_writer_conflicts,
    )

    patch_a = (
        "diff --git a/service.py b/service.py\n"
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 2\n"
    )
    patch_b = (
        "diff --git a/service.py b/service.py\n"
        "--- a/service.py\n"
        "+++ b/service.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 3\n"
    )
    owned: dict[str, str] = {}
    assert not detect_writer_conflicts(owned, changed_paths_from_patch(patch_a), "T-A")
    conflicts = detect_writer_conflicts(owned, changed_paths_from_patch(patch_b), "T-B")
    assert conflicts
    assert conflicts[0]["path"] == "service.py"


def test_file_list_context_mode_omits_file_bodies(tmp_path: Path) -> None:
    from product_factory.context.assembler import list_repository_paths

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cache.py").write_text("SECRET_BODY = 1\n", encoding="utf-8")
    excerpts, omissions = list_repository_paths(repo)
    assert excerpts[0]["path"] == "cache.py"
    assert excerpts[0]["content"] == "(path only)"
    assert any("file_list_only" in item for item in omissions)


def test_force_review_injects_review_with_acceptance_criteria() -> None:
    from product_factory.config.loader import load_config
    from product_factory.domain.runs import RunRequest
    from product_factory.gateway.mock import MockGateway
    from product_factory.orchestration.coordinator import RunCoordinator
    from product_factory.planning.compiler import compile_plan

    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=root / ".product-factory-test-unused",
        use_deterministic_planner=True,
    )
    proposal = coordinator._plan(
        "run",
        RunRequest(
            request_id="force-review",
            workflow_type="code_change",
            request_text="Add a cache helper",
            approval_policy="none",
            metadata={"force_review": "true", "planner_mode": "fixed"},
        ),
        None,
    )
    assert any(task.capability == "independent_review" for task in proposal.tasks)
    result = compile_plan(proposal)
    assert result.ok, result.errors


def test_validation_repair_ablation_strips_analysis(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=EmptyLiveGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    proposal = coordinator._plan(
        "run",
        RunRequest(
            request_id="ablation",
            workflow_type="code_change",
            request_text="Change authentication and database permissions",
            approval_policy="none",
            metadata={
                "disable_review": "true",
                "disable_analysis": "true",
                "planner_mode": "fixed",
            },
        ),
        None,
    )
    assert "repository_analysis" not in {task.capability for task in proposal.tasks}
    assert "independent_review" not in {task.capability for task in proposal.tasks}
    assert "implementation" in {task.capability for task in proposal.tasks}
