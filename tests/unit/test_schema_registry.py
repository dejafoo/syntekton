"""PM0 schema registry unit tests."""

from __future__ import annotations

import pytest

from product_factory.domain.artifacts import ArtifactRef, HandoffRef
from product_factory.domain.errors import SchemaValidationError
from product_factory.schemas import (
    ROLE_TO_SCHEMA,
    SchemaRegistry,
    SchemaSpec,
    assert_schema_writable,
    default_schema_registry,
    read_schema_metadata,
    reset_default_schema_registry,
    resolve_output_schema_id,
    seed_builtin_schemas,
    validate_handoff_ref_shape,
    validate_write_payload,
)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_default_schema_registry()
    yield
    reset_default_schema_registry()


def test_seed_round_trip() -> None:
    reg = default_schema_registry()
    assert reg.known("evidence_report.document.v1")
    assert reg.known("evidence_report.document.v2")
    assert reg.known("feasibility_dossier.v1")
    spec = reg.require("technical_plan.document.v1", for_write=True)
    assert spec.kind == "task_output"
    assert reg.require("technical_plan.document.v2", for_write=True).version == "2"


def test_pm3a_roles_emit_v2_contracts_with_v1_read_compatibility() -> None:
    reg = default_schema_registry()
    assert ROLE_TO_SCHEMA["evidence_report"] == "evidence_report.document.v2"
    assert ROLE_TO_SCHEMA["architecture_document"] == "technical_plan.document.v2"
    assert reg.require("evidence_report.document.v1", for_write=False).version == "1"
    assert reg.require("technical_plan.document.v1", for_write=False).version == "1"
    assert resolve_output_schema_id("evidence_report.v2") == "evidence_report.document.v2"
    assert resolve_output_schema_id("technical_plan.v2") == "technical_plan.document.v2"


def test_unknown_schema_write_fails() -> None:
    with pytest.raises(SchemaValidationError, match="Unknown schema_id"):
        assert_schema_writable("not.a.schema.v1")


def test_reserved_schema_write_fails() -> None:
    with pytest.raises(SchemaValidationError, match="reserved"):
        assert_schema_writable("verification_report.v1")


def test_feasibility_dossier_is_writable_after_pm1() -> None:
    """PM1.0 un-reserves the dossier so a discovery pack can emit it."""
    reg = default_schema_registry()
    spec = reg.require("feasibility_dossier.v1", for_write=True)
    assert spec.kind == "task_output"
    assert spec.reserved is False
    assert assert_schema_writable("feasibility_dossier.v1") == "feasibility_dossier.v1"
    assert ROLE_TO_SCHEMA["feasibility_dossier"] == "feasibility_dossier.v1"


def test_change_brief_and_clarification_are_writable_after_pm2() -> None:
    """PM2.0 un-reserves change_brief and registers clarification_request."""
    reg = default_schema_registry()
    for schema_id, role in (
        ("change_brief.v1", "change_brief"),
        ("clarification_request.v1", "clarification_request"),
    ):
        spec = reg.require(schema_id, for_write=True)
        assert spec.kind == "task_output"
        assert spec.reserved is False
        assert assert_schema_writable(schema_id) == schema_id
        assert ROLE_TO_SCHEMA[role] == schema_id
    validate_write_payload(
        "change_brief.v1",
        {
            "outcome": "Add rate limiting",
            "scope": "API gateway",
            "non_goals": ["UI redesign"],
            "acceptance_criteria": ["429 on burst"],
            "constraints": ["No new deps"],
            "risks": ["False positives"],
            "assumptions": ["Existing auth stays"],
            "unknowns": ["Exact burst window"],
            "recommended_next_pack": "technical_plan",
        },
    )
    validate_write_payload(
        "clarification_request.v1",
        {
            "questions": ["What outcome?"],
            "blocking_unknowns": ["Acceptance"],
            "partial_outcome": "Improve something",
            "recommended_next_pack": None,
        },
    )


@pytest.mark.parametrize(
    ("schema_id", "kind", "payload"),
    [
        (
            "source_capture.v1",
            "source_record",
            {
                "url": "https://example.test/spec",
                "sha256": "c" * 64,
                "media_type": "text/html",
                "bytes": 42,
                "retrieved_at": "2026-01-01T00:00:00Z",
            },
        ),
        ("research_ledger.v1", "task_output", {"entries": []}),
        (
            "decision_record.v1",
            "task_output",
            {"decision_statement": "Adopt X?", "recommendation": "insufficient_evidence"},
        ),
        (
            "option_matrix.v1",
            "task_output",
            {"options": ["a", "b"], "criteria": ["cost"], "cells": {}},
        ),
    ],
)
def test_pm1_evidence_schemas_registered(schema_id: str, kind: str, payload: dict) -> None:
    reg = default_schema_registry()
    assert reg.require(schema_id, for_write=True).kind == kind
    validate_write_payload(schema_id, payload)


def test_pm1_schema_required_fields_fail_closed() -> None:
    with pytest.raises(SchemaValidationError, match="missing required fields"):
        validate_write_payload("option_matrix.v1", {"options": ["a"]})


def test_pm1_legacy_aliases_resolve_to_canonical_ids() -> None:
    assert resolve_output_schema_id("feasibility_dossier.document.v1") == "feasibility_dossier.v1"
    assert resolve_output_schema_id("feasibility_discovery.v1") == "feasibility_dossier.v1"
    assert resolve_output_schema_id("option_matrix.document.v1") == "option_matrix.v1"
    assert resolve_output_schema_id("change_brief.document.v1") == "change_brief.v1"
    assert resolve_output_schema_id("clarification_request.document.v1") == "clarification_request.v1"


def test_spike_result_is_writable_after_pm3b() -> None:
    """PM3.B un-reserves spike_result.v1 for the technical_spike pack."""
    reg = default_schema_registry()
    spec = reg.require("spike_result.v1", for_write=True)
    assert spec.kind == "task_output"
    assert spec.reserved is False
    assert assert_schema_writable("spike_result.v1") == "spike_result.v1"


def test_remaining_reserved_ids_still_block_writes() -> None:
    reg = default_schema_registry()
    for schema_id in (
        "verification_report.v1",
        "release_plan.v1",
        "deployment_record.v1",
        "operational_record.v1",
    ):
        assert reg.require(schema_id, for_write=False).reserved
        with pytest.raises(SchemaValidationError, match="reserved"):
            assert_schema_writable(schema_id)


def test_pm0_era_readers_are_unaffected_by_pm1_registrations() -> None:
    """A PM0 run's recorded schema ids keep resolving to the same contracts."""
    for schema_id, kind in (
        ("evidence_report.document.v1", "task_output"),
        ("technical_plan.document.v1", "task_output"),
        ("change_set.patch.v1", "task_output"),
        ("source_record.v1", "source_record"),
        ("connector_receipt.v1", "tool_receipt"),
    ):
        meta = read_schema_metadata(schema_id)
        assert meta == {
            "schema_id": schema_id,
            "known": True,
            "kind": kind,
            "version": "1",
            "reserved": False,
            "opaque": False,
        }
    assert resolve_output_schema_id("architecture_doc.v1") == "technical_plan.document.v1"
    assert resolve_output_schema_id("review_findings.v1") == "quality_findings.document.v1"


def test_unknown_schema_read_opaque() -> None:
    meta = read_schema_metadata("future.unknown.v9")
    assert meta["known"] is False
    assert meta["opaque"] is True
    assert meta["warning"] == "unknown_schema_id"


def test_legacy_output_schema_map() -> None:
    assert resolve_output_schema_id("architecture_doc.v1") == "technical_plan.document.v1"
    validate_write_payload("architecture_doc.v1", {"text": "ok"})


def test_artifact_ref_schema_fields() -> None:
    ref = ArtifactRef(
        sha256="a" * 64,
        media_type="text/markdown",
        size_bytes=1,
        logical_name="plan.md",
        relative_path="plan.md",
        created_by_task_id="t1",
        schema_id="technical_plan.document.v1",
        schema_version="1",
        handoff_state="draft",
    )
    assert ref.schema_id == "technical_plan.document.v1"


def test_handoff_ref_validation() -> None:
    ref = HandoffRef(
        schema_id="technical_plan.document.v1",
        digest="b" * 64,
        producer_run_id="run-1",
        producer_task_id="t1",
        role="architecture_document",
        state="approved",
    )
    validate_handoff_ref_shape(ref.model_dump())
    with pytest.raises(SchemaValidationError):
        validate_handoff_ref_shape({"schema_id": "technical_plan.document.v1"})


def test_custom_registry_register() -> None:
    reg = SchemaRegistry()
    seed_builtin_schemas(reg)
    with pytest.raises(Exception):
        reg.register(SchemaSpec(id="evidence_report.document.v1", version="1", kind="task_output"))
