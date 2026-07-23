"""Isolation runner must use the agent-loop path and emit applying patches."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.runners import IsolationAblationRunner
from product_factory.evaluation.subjects import SubjectConfig
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_implementation_isolation_produces_non_empty_patch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    runner = IsolationAblationRunner(config, use_deterministic_planner=True)
    case = EvalCase(
        id="code_cache",
        workflow_type="code_change",
        request="Introduce a simple cache helper behind an interface.",
        repository="tests/fixtures/sample_api",
        expected_files=["src/app/cache.py", "tests/test_cache.py"],
        smoke_commands=["python_tests"],
        budgets={"max_cost_usd": "1.00"},
    )
    artifact = runner.run(
        case,
        config=SubjectConfig(
            subject_id="implementation_isolation",
            model_profile="coding_worker",
        ),
        gateway=MockGateway(),
        work_dir=tmp_path / "work",
    )
    assert artifact.subject_id == "implementation_isolation"
    assert not artifact.error
    assert artifact.artifact_text.strip(), "isolation must emit a non-empty patch"
    assert artifact.artifact_kind == "patch"
    assert "cache" in artifact.artifact_text.lower()
    assert artifact.metadata.get("isolation_mode") == "agent_loop"


def test_isolation_disables_validation_repair(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    proposal = coordinator._plan(
        "run",
        RunRequest(
            request_id="iso",
            workflow_type="code_change",
            request_text="Introduce a simple cache helper behind an interface.",
            approval_policy="none",
            metadata={
                "disable_review": "true",
                "disable_analysis": "true",
                "disable_validation_repair": "true",
                "planner_mode": "fixed",
            },
        ),
        None,
    )
    caps = {task.capability for task in proposal.tasks}
    assert "implementation" in caps
    assert "independent_review" not in caps
    assert "repository_analysis" not in caps


def test_force_review_mock_end_to_end_produces_patch(tmp_path: Path) -> None:
    from product_factory.evaluation.runners import OrchestrationAblationRunner

    root = Path(__file__).resolve().parents[2]
    runner = OrchestrationAblationRunner(
        load_config(root),
        subject_id="full_orchestration_with_review",
        metadata={"force_review": True, "planner_mode": "fixed"},
        use_deterministic_planner=True,
    )
    case = EvalCase(
        id="code_cache",
        workflow_type="code_change",
        request="Introduce a simple cache helper behind an interface.",
        repository="tests/fixtures/sample_api",
        expected_files=["src/app/cache.py"],
        smoke_commands=["python_tests"],
        budgets={"max_cost_usd": "1.00"},
    )
    artifact = runner.run(
        case,
        config=SubjectConfig(
            subject_id="full_orchestration_with_review",
            model_profile="supervisor",
        ),
        gateway=MockGateway(),
        work_dir=tmp_path / "review-work",
    )
    assert not artifact.error, artifact.error
    assert artifact.artifact_text.strip()
    assert "cache" in artifact.artifact_text.lower()
