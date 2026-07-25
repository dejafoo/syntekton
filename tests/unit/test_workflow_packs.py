"""Unit tests for the workflow pack registry (P1.G / P3.D)."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.orchestration.coordinator import (
    default_architecture_plan,
    default_investigation_plan,
    default_quality_gate_plan,
    default_technical_plan,
)
from product_factory.validation.pipeline import (
    validate_citations,
    validate_document_sections,
    validate_investigation_document,
    validate_secrets,
)
from product_factory.workflows import (
    WorkflowPack,
    canonical_workflow_id,
    list_workflow_packs,
    resolve_workflow_pack,
)
from product_factory.workflows.artifacts import resolve_artifact_land_map
from product_factory.workflows.quality_gate import (
    QUALITY_GATE_PACK,
    QUALITY_GATE_REQUIRED_SECTIONS,
    QUALITY_GATE_VALIDATOR_IDS,
)
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK
from product_factory.workflows.repository_investigation import REPOSITORY_INVESTIGATION_PACK
from product_factory.workflows.technical_plan import TECHNICAL_PLAN_PACK


def test_registry_lists_all_packs() -> None:
    packs = list_workflow_packs()
    ids = {p.id for p in packs}
    assert "repository_change" in ids
    assert "repository_investigation" in ids
    assert "technical_plan" in ids
    assert "quality_gate" in ids


def test_resolve_repository_change_directly() -> None:
    pack = resolve_workflow_pack("repository_change")
    assert pack is REPOSITORY_CHANGE_PACK
    assert pack.version == "1.0.0"


def test_code_change_aliases_to_repository_change() -> None:
    assert canonical_workflow_id("code_change") == "repository_change"
    pack = resolve_workflow_pack("code_change")
    assert pack.id == "repository_change"


def test_architecture_aliases_to_technical_plan() -> None:
    assert canonical_workflow_id("architecture") == "technical_plan"
    pack = resolve_workflow_pack("architecture")
    assert pack is TECHNICAL_PLAN_PACK
    assert pack.id == "technical_plan"
    assert pack.version == "1.0.0"


def test_technical_plan_and_architecture_alias_parity() -> None:
    direct = resolve_workflow_pack("technical_plan")
    aliased = resolve_workflow_pack("architecture")
    assert direct is aliased
    assert direct.content_hash() == aliased.content_hash()
    assert (
        default_technical_plan("Design X").model_dump()
        == default_architecture_plan("Design X").model_dump()
    )


def test_resolve_repository_investigation() -> None:
    pack = resolve_workflow_pack("repository_investigation")
    assert pack is REPOSITORY_INVESTIGATION_PACK
    assert pack.version == "1.0.0"
    assert "implementation" not in pack.allowed_capabilities
    assert "repair" not in pack.allowed_capabilities
    assert pack.validation_policy.get("write_grants") == "none"


def test_unknown_workflow_id_fails_closed() -> None:
    with pytest.raises(ConfigurationError):
        resolve_workflow_pack("totally_unknown_workflow")


def test_pack_hash_is_stable_and_present_in_manifest_metadata() -> None:
    pack = resolve_workflow_pack("repository_change")
    meta = pack.manifest_metadata()
    assert meta["workflow_pack_id"] == "repository_change"
    assert meta["workflow_pack_version"] == "1.0.0"
    assert meta["workflow_pack_hash"] == pack.content_hash()
    # Deterministic across calls.
    assert pack.content_hash() == pack.content_hash()


def test_pack_hash_changes_when_content_changes() -> None:
    base = resolve_workflow_pack("repository_change")
    mutated = WorkflowPack(
        id=base.id,
        version=base.version,
        input_schema=base.input_schema,
        output_schema=base.output_schema,
        allowed_capabilities=base.allowed_capabilities,
        default_planner_mode=base.default_planner_mode,
        validation_policy={**base.validation_policy, "extra": True},
        skill_policy=base.skill_policy,
        routing_defaults=base.routing_defaults,
    )
    assert mutated.content_hash() != base.content_hash()


def test_repository_change_wraps_code_change_capabilities() -> None:
    pack = resolve_workflow_pack("repository_change")
    assert "implementation" in pack.allowed_capabilities
    assert "independent_review" in pack.allowed_capabilities
    assert "composition" in pack.allowed_capabilities


def test_pack_never_carries_executable_code_only_data() -> None:
    for workflow_id in (
        "repository_change",
        "repository_investigation",
        "technical_plan",
        "quality_gate",
    ):
        pack = resolve_workflow_pack(workflow_id)
        assert pack.default_planner_mode == "fixed"
        for field in (
            pack.input_schema,
            pack.output_schema,
            pack.validation_policy,
            pack.skill_policy,
            pack.routing_defaults,
        ):
            assert isinstance(field, dict)


def test_investigation_fixed_plan_has_no_write_tool_classes() -> None:
    proposal = default_investigation_plan("Where is auth handled?")
    write_classes = {"repository_write", "git_write", "validation_command"}
    for task in proposal.tasks:
        assert not (task.required_tool_classes & write_classes)
        assert "file_write" in task.prohibited_actions
        assert "repository_write" in task.prohibited_actions


def test_investigation_section_validator() -> None:
    ok = """# EVIDENCE_REPORT.md

## Summary
Auth lives in the API layer.

## Findings
- Middleware checks tokens — see `src/api/auth.py`

## Cited paths
- `src/api/auth.py`
- `src/api/app.py`

## Assumptions
- Read-only inspection only.
"""
    assert validate_investigation_document(ok).status == "pass"
    missing = validate_investigation_document("# Report\n\n## Summary\nOnly summary.\n")
    assert missing.status == "fail"
    assert "Cited paths" in missing.details["missing_sections"]


def test_citation_validator_requires_path_like_backticks() -> None:
    with_cite = "See `src/main.py` and `README.md` for entry points."
    result = validate_citations(with_cite)
    assert result.status == "pass"
    assert "src/main.py" in result.details["citations"]

    without = validate_citations("No paths here, only prose.")
    assert without.status == "fail"
    assert without.validator_id == "citation_presence"


def test_resolve_quality_gate_pack() -> None:
    pack = resolve_workflow_pack("quality_gate")
    assert pack is QUALITY_GATE_PACK
    assert pack.version == "1.0.0"
    assert "implementation" not in pack.allowed_capabilities
    assert "repair" not in pack.allowed_capabilities
    assert pack.validation_policy.get("write_grants") == "none"
    # Blocking findings are the product of this pack, not a run failure.
    assert pack.validation_policy.get("findings_are_deliverable") is True


def test_quality_gate_declares_three_deliverable_roles() -> None:
    pack = resolve_workflow_pack("quality_gate")
    roles = [spec.role for spec in pack.artifacts]
    assert roles == ["test_plan", "quality_findings", "security_evidence"]
    by_role = {spec.role: spec for spec in pack.artifacts}
    assert by_role["test_plan"].default_dest_path == "docs/TEST_PLAN.md"
    assert by_role["quality_findings"].default_dest_path == "docs/QUALITY_FINDINGS.md"
    assert by_role["security_evidence"].default_dest_path == "docs/SECURITY_EVIDENCE.md"
    # Every quality deliverable can be renamed and landed by a host.
    assert all(spec.landable and spec.renamable for spec in pack.artifacts)
    # Security evidence is omitted rather than landed empty when no security task ran.
    assert by_role["security_evidence"].required is False
    assert by_role["test_plan"].required is True


def test_quality_gate_land_map_resolves_defaults_and_overrides() -> None:
    pack = resolve_workflow_pack("quality_gate")
    land_map = resolve_artifact_land_map(pack.artifacts)
    assert land_map.logical_name_for("test_plan", default="x") == "TEST_PLAN.md"

    renamed = resolve_artifact_land_map(
        pack.artifacts,
        overrides={"test_plan": {"dest_path": "docs/qa/integration_test_plan.md"}},
    )
    assert renamed.logical_name_for("test_plan", default="x") == "integration_test_plan.md"
    assert renamed.dest_path_for("test_plan") == "docs/qa/integration_test_plan.md"
    # Sibling roles are untouched by another role's override.
    assert renamed.dest_path_for("quality_findings") == "docs/QUALITY_FINDINGS.md"


def test_quality_gate_fixed_plan_is_read_only_with_one_composer_per_role() -> None:
    proposal = default_quality_gate_plan("Assess release readiness.")
    write_classes = {"repository_write", "git_write"}
    for task in proposal.tasks:
        assert not (task.required_tool_classes & write_classes)
        assert "repository_write" in task.prohibited_actions

    roles = [spec.role for spec in proposal.final_artifacts]
    assert sorted(roles) == ["quality_findings", "security_evidence", "test_plan"]
    composers = [spec.composer_task_id for spec in proposal.final_artifacts]
    assert len(set(composers)) == len(composers), "each role needs its own composer task"
    task_ids = {task.id for task in proposal.tasks}
    assert set(composers) <= task_ids


def test_quality_gate_section_validator_keys_off_content_not_filename() -> None:
    body = (
        "# release_readiness.md\n\n## Summary\nScoped.\n\n## Findings\n- none\n\n"
        "## Evidence\n- `src/app/main.py`\n\n## Recommended actions\n- none\n"
    )
    passing = validate_document_sections(
        body,
        validator_id=QUALITY_GATE_VALIDATOR_IDS["quality_findings"],
        required_sections=QUALITY_GATE_REQUIRED_SECTIONS["quality_findings"],
    )
    assert passing.status == "pass"

    incomplete = validate_document_sections(
        "# QUALITY_FINDINGS.md\n\n## Summary\nOnly a summary.\n",
        validator_id=QUALITY_GATE_VALIDATOR_IDS["quality_findings"],
        required_sections=QUALITY_GATE_REQUIRED_SECTIONS["quality_findings"],
    )
    assert incomplete.status == "fail"
    assert "Recommended actions" in incomplete.details["missing_sections"]


def test_investigation_secret_scan_still_applies() -> None:
    dirty = (
        "# EVIDENCE_REPORT.md\n\n## Summary\nx\n\n## Findings\n- y\n\n"
        "## Cited paths\n- `a.py`\n\n## Assumptions\n- z\n\n"
        "api_key = 'SUPERSECRETVALUE'\n"
    )
    assert validate_secrets(dirty).status == "fail"
