"""Graph tests for the change_intake pack (PM2.A)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.validation.pipeline import validate_intake_sections
from product_factory.workflows.artifacts import ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST
from tests.conftest import clone_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "intake"


def _coord(tmp_path: Path) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    return RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_ambiguous_request_lands_clarification(tmp_path: Path) -> None:
    request_text = (FIXTURES / "ambiguous_request.md").read_text(encoding="utf-8")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-intake-ambig",
            workflow_type="change_intake",
            request_text=request_text,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    clarification = output / "CLARIFICATION_REQUEST.md"
    assert clarification.exists()
    assert not (output / "CHANGE_BRIEF.md").exists()
    body = clarification.read_text(encoding="utf-8")
    assert validate_intake_sections(body, role=ROLE_CLARIFICATION_REQUEST).status == "pass"
    tool_names = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
    assert "create_file" not in tool_names
    assert "apply_patch" not in tool_names
    assert "run_validation_command" not in tool_names
    assert "web_search" not in tool_names
    assert "fetch_source" not in tool_names


def test_well_scoped_feature_lands_change_brief(tmp_path: Path) -> None:
    request_text = (FIXTURES / "well_scoped_feature.md").read_text(encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-intake-feature",
            workflow_type="change_intake",
            request_text=request_text,
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            pack_input={
                "desired_outcome": "Health endpoint returns ok",
                "known_constraints": ["Stay in src/api"],
            },
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    brief = output / "CHANGE_BRIEF.md"
    assert brief.exists()
    assert not (output / "CLARIFICATION_REQUEST.md").exists()
    body = brief.read_text(encoding="utf-8")
    assert validate_intake_sections(body, role=ROLE_CHANGE_BRIEF).status == "pass"
    assert "technical_plan" in body.lower()


def test_well_scoped_defect_lands_change_brief(tmp_path: Path) -> None:
    request_text = (FIXTURES / "well_scoped_defect.md").read_text(encoding="utf-8")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-intake-defect",
            workflow_type="change_intake",
            request_text=request_text,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    brief = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output" / "CHANGE_BRIEF.md"
    assert brief.exists()
    assert (
        validate_intake_sections(brief.read_text(encoding="utf-8"), role=ROLE_CHANGE_BRIEF).status
        == "pass"
    )


def test_intake_honors_renamed_brief(tmp_path: Path) -> None:
    request_text = (FIXTURES / "well_scoped_feature.md").read_text(encoding="utf-8")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-intake-rename",
            workflow_type="change_intake",
            request_text=request_text,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            artifact_overrides={
                "change_brief": ArtifactOverride(dest_path="docs/health_change_brief.md")
            },
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    assert (output / "health_change_brief.md").exists()
    assert not (output / "CHANGE_BRIEF.md").exists()


def test_technical_plan_accepts_change_brief_handoff_pin(tmp_path: Path) -> None:
    """Planning can consume a change_brief by pin (shape-level handoff smoke)."""
    import shutil

    project = tmp_path / "project"
    project.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "config", project / "config")
    shutil.copytree(repo_root / "profiles", project / "profiles")
    host = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    ref = HandoffRef(
        schema_id="change_brief.v1",
        digest="a" * 64,
        producer_run_id="run-intake-1",
        producer_task_id="T-003",
        role="change_brief",
        state="approved",
    )
    accepted = host.submit(
        RunRequest(
            request_id="plan-from-brief",
            workflow_type="technical_plan",
            request_text="Plan the health endpoint from the pinned change brief.",
            handoff_refs=[ref],
        ),
        mock=True,
        detach=False,
    )
    assert accepted.ok, accepted.error


def test_host_rejects_unknown_pack_input(tmp_path: Path) -> None:
    import shutil

    project = tmp_path / "project"
    project.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "config", project / "config")
    shutil.copytree(repo_root / "profiles", project / "profiles")
    host = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    rejected = host.submit(
        RunRequest(
            request_id="bad-intake",
            workflow_type="change_intake",
            request_text="Add something",
            pack_input={"not_a_field": True},
        ),
        mock=True,
        detach=False,
    )
    assert not rejected.ok
    assert rejected.error is not None
    assert rejected.error.code == "invalid_pack_input"
