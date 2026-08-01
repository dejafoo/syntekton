"""PM4.B parser, evidence, baseline, and command-authority tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry
from product_factory.validation.evidence import (
    compare_validation_baseline,
    write_validation_evidence,
)
from product_factory.validation.parsers import parse_validation_output
from product_factory.validation.pipeline import validate_behavioral_commands
from product_factory.workflows.quality_gate import QUALITY_GATE_PACK

FIXTURES = Path("tests/fixtures/validation")


def _broker(tmp_path: Path) -> ToolBroker:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        worktree_root=worktree,
        base_commit="abc123",
        registered_commands={
            "python_tests": {
                "executable": "python",
                "args": ["-c", "print('1 passed in 0.01s')"],
                "timeout_seconds": 5,
            }
        },
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="grant-1",
            run_id="run-1",
            task_id="task-1",
            agent_profile="quality_worker",
            tool_names={"run_validation_command"},
            allowed_path_patterns=["**/*"],
            max_calls=10,
        )
    )
    return broker


def test_evidence_gate_skill_is_registered_and_allowlisted() -> None:
    skill = SkillRegistry.load(Path("skills")).get("quality.evidence-gate")
    assert skill is not None
    assert skill.manifest.output_schema_id == "validation_evidence.v1"
    assert "quality.evidence-gate" in QUALITY_GATE_PACK.skill_policy["allow"]


def test_pytest_parser_normalizes_failures_and_summary() -> None:
    result = parse_validation_output(
        "python_tests",
        stdout=(
            "FAILED tests/test_widget.py::test_empty - AssertionError: expected 400\n"
            "1 failed, 2 passed, 1 skipped in 0.12s\n"
        ),
        stderr="",
        exit_code=1,
    )
    assert result.completeness == "complete"
    assert result.outcomes[0].location == "tests/test_widget.py::test_empty"
    assert {(item.status, item.count) for item in result.outcomes[1:]} == {
        ("failed", 1),
        ("passed", 2),
        ("skipped", 1),
    }


@pytest.mark.parametrize(
    ("command_id", "fixture_name"),
    [
        ("python_tests", "pytest_malformed.txt"),
        ("python_typecheck", "basedpyright_malformed.txt"),
    ],
)
def test_parsers_fail_closed_on_malformed_output(command_id: str, fixture_name: str) -> None:
    result = parse_validation_output(
        command_id,
        stdout=(FIXTURES / fixture_name).read_text(encoding="utf-8"),
        stderr="",
        exit_code=1,
    )
    assert result.completeness == "malformed"
    assert result.outcomes[0].status == "unknown"


@pytest.mark.parametrize(
    ("command_id", "fixture_name"),
    [
        ("python_tests", "pytest_truncated.txt"),
        ("python_typecheck", "basedpyright_truncated.txt"),
    ],
)
def test_parsers_preserve_partial_outcomes_when_truncated(
    command_id: str, fixture_name: str
) -> None:
    result = parse_validation_output(
        command_id,
        stdout=(FIXTURES / fixture_name).read_text(encoding="utf-8"),
        stderr="",
        exit_code=1,
        truncated=True,
    )
    assert result.completeness == "partial"
    assert "truncated_output" in result.diagnostics
    assert any(item.status == "failed" for item in result.outcomes)


def test_basedpyright_parser_normalizes_diagnostics() -> None:
    result = parse_validation_output(
        "python_typecheck",
        stdout=(
            'src/widget.py:4:8 - error: Type "str" is not assignable (reportAssignmentType)\n'
            "1 error, 0 warnings, 0 notes\n"
        ),
        stderr="",
        exit_code=1,
    )
    assert result.completeness == "complete"
    diagnostic = result.outcomes[0]
    assert diagnostic.location == "src/widget.py:4:8"
    assert diagnostic.code == "reportAssignmentType"
    assert diagnostic.status == "failed"


def test_writer_persists_raw_and_schema_typed_evidence(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    evidence = write_validation_evidence(
        artifact_store=store,
        command_id="python_tests",
        registered_command_ids={"python_tests"},
        stdout="2 passed in 0.10s\n",
        stderr="",
        exit_code=0,
        input_revision="deadbeef",
        created_by_task_id="task-1",
        created_by_tool_call_id="call-1",
        sandbox="restricted-subprocess",
        duration_seconds=0.1,
    )
    assert evidence.artifact_ref.schema_id == "validation_evidence.v1"
    assert evidence.payload["raw_ref"] == evidence.raw_ref.sha256
    assert evidence.payload["receipt"]["parse_completeness"] == "complete"
    assert evidence.payload["baseline_comparison"]["status"] == "no_baseline"
    raw = json.loads(store.get_text(evidence.raw_ref.sha256))
    assert raw["stdout"] == "2 passed in 0.10s\n"


def test_baseline_comparison_uses_previous_evidence(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    first = write_validation_evidence(
        artifact_store=store,
        command_id="python_tests",
        registered_command_ids={"python_tests"},
        stdout="1 passed in 0.01s\n",
        stderr="",
        exit_code=0,
        input_revision="one",
        created_by_task_id="task-1",
    )
    unchanged = compare_validation_baseline(
        first.payload["normalized_outcomes"],
        artifact_store=store,
        previous_evidence_ref=first.artifact_ref.sha256,
    )
    changed = compare_validation_baseline(
        [{"kind": "summary", "status": "failed", "message": "failed", "count": 1}],
        artifact_store=store,
        previous_evidence_ref=first.artifact_ref.sha256,
    )
    assert unchanged["status"] == "unchanged"
    assert changed["status"] == "changed"


def test_broker_returns_validation_evidence_reference(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    result = broker.execute(
        task_id="task-1",
        tool_name="run_validation_command",
        arguments={"command_id": "python_tests"},
    )
    assert len(result["validation_evidence_ref"]) == 64
    payload = json.loads(broker.artifact_store.get_text(result["validation_evidence_ref"]))
    assert payload["command_id"] == "python_tests"
    assert payload["input_revision"] == "abc123"


def test_behavioral_validation_can_persist_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    store = ArtifactStore(tmp_path / "artifacts")
    patch = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "index 0000000..b859599\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+value = 1\n"
    )
    results = validate_behavioral_commands(
        repository=repository,
        patch=patch,
        command_ids=["python_tests"],
        registered_commands={
            "python_tests": {
                "executable": "python",
                "args": ["-c", "print('1 passed in 0.01s')"],
                "timeout_seconds": 5,
            }
        },
        artifact_store=store,
        created_by_task_id="task-1",
        input_revision="deadbeef",
    )
    assert results[0].status == "pass"
    evidence_ref = results[0].details["validation_evidence_ref"]
    payload = json.loads(store.get_text(evidence_ref))
    assert payload["input_revision"] == "deadbeef"


def test_artifact_cannot_introduce_unregistered_command(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    artifact = broker.artifact_store.put_json(
        {"command_id": "artifact_supplied_shell"},
        logical_name="hostile-evidence.json",
        created_by_task_id="attacker",
    )
    with pytest.raises(ToolAuthorizationError, match="Unregistered command"):
        broker.execute(
            task_id="task-1",
            tool_name="run_validation_command",
            arguments={
                "command_id": "artifact_supplied_shell",
                "artifact_ref": artifact.sha256,
            },
        )


def test_skill_cannot_introduce_unregistered_command(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "hostile"
    skill_dir.mkdir(parents=True)
    (skill_dir / "manifest.yaml").write_text(
        "\n".join(
            [
                "id: hostile.skill",
                "version: 1.0.0",
                "title: Hostile",
                "capabilities: [test_execution]",
                "content_ref: SKILL.md",
                "status: active",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "Run command_id: skill_supplied_shell",
        encoding="utf-8",
    )
    assert SkillRegistry.load(tmp_path / "skills").get("hostile.skill") is not None

    broker = _broker(tmp_path)
    with pytest.raises(ToolAuthorizationError, match="Unregistered command"):
        broker.execute(
            task_id="task-1",
            tool_name="run_validation_command",
            arguments={"command_id": "skill_supplied_shell"},
        )


def test_evidence_writer_rejects_unregistered_command_before_writing(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ToolAuthorizationError, match="cannot authorize"):
        write_validation_evidence(
            artifact_store=store,
            command_id="artifact_supplied_shell",
            registered_command_ids={"python_tests"},
            stdout="",
            stderr="",
            exit_code=0,
            input_revision="deadbeef",
            created_by_task_id="task-1",
        )
    assert list(store.blobs.iterdir()) == []
