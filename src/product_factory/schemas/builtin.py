"""Seed schemas for current packs plus reserved future ids."""

from __future__ import annotations

from product_factory.schemas.registry import SchemaRegistry, SchemaSpec

# Legacy TaskSpec.expected_output_schema strings → registry ids.
LEGACY_OUTPUT_SCHEMA_MAP: dict[str, str] = {
    "repository_analysis.v1": "repository_analysis.report.v1",
    "implementation_result.v1": "change_set.patch.v1",
    "implementation.v1": "change_set.patch.v1",
    "review_findings.v1": "quality_findings.document.v1",
    "composition_result.v1": "change_set.patch.v1",
    "architecture_doc.v1": "technical_plan.document.v1",
    "architecture_partial.v1": "technical_plan.document.v1",
    "architecture.v1": "technical_plan.document.v1",
    "requirements.v1": "technical_plan.document.v1",
    "evidence_report.v1": "evidence_report.document.v1",
    "test_plan.v1": "test_plan.document.v1",
    "quality_findings.v1": "quality_findings.document.v1",
    "security_evidence.v1": "security_evidence.document.v1",
    "documentation.v1": "technical_plan.document.v1",
    "test_design.v1": "test_plan.document.v1",
    "test_execution.v1": "quality_findings.document.v1",
    "security_review.v1": "security_evidence.document.v1",
    # PM1 discovery outputs — accept the `<role>.document.v1` spelling a planner
    # may emit by convention alongside the canonical bare ids.
    "feasibility_dossier.document.v1": "feasibility_dossier.v1",
    "feasibility_discovery.v1": "feasibility_dossier.v1",
    "decision_record.document.v1": "decision_record.v1",
    "option_matrix.document.v1": "option_matrix.v1",
    "research_ledger.document.v1": "research_ledger.v1",
    # PM2 intake outputs — accept `<role>.document.v1` alongside bare ids.
    "change_brief.document.v1": "change_brief.v1",
    "clarification_request.document.v1": "clarification_request.v1",
    "change_intake.v1": "change_brief.v1",
}

ROLE_TO_SCHEMA: dict[str, str] = {
    "evidence_report": "evidence_report.document.v1",
    "architecture_document": "technical_plan.document.v1",
    "proposed_patch": "change_set.patch.v1",
    "test_plan": "test_plan.document.v1",
    "quality_findings": "quality_findings.document.v1",
    "security_evidence": "security_evidence.document.v1",
    "feasibility_dossier": "feasibility_dossier.v1",
    "change_brief": "change_brief.v1",
    "clarification_request": "clarification_request.v1",
}


def resolve_output_schema_id(raw: str) -> str:
    """Map a task expected_output_schema string to a registry id."""
    value = (raw or "").strip()
    if not value:
        return value
    return LEGACY_OUTPUT_SCHEMA_MAP.get(value, value)


def seed_builtin_schemas(registry: SchemaRegistry) -> None:
    document = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
    }
    for schema_id, description in (
        ("evidence_report.document.v1", "Cited repository evidence report"),
        ("technical_plan.document.v1", "Architecture / technical plan markdown"),
        ("test_plan.document.v1", "Quality-gate test plan"),
        ("quality_findings.document.v1", "Quality findings report"),
        ("security_evidence.document.v1", "Security evidence report"),
        ("repository_analysis.report.v1", "Repository analysis JSON report"),
        ("feasibility_dossier.v1", "Feasibility discovery dossier"),
    ):
        registry.register(
            SchemaSpec(
                id=schema_id,
                version="1",
                kind="task_output",
                json_schema=document,
                description=description,
            )
        )

    registry.register(
        SchemaSpec(
            id="change_set.patch.v1",
            version="1",
            kind="task_output",
            json_schema={"type": "object", "properties": {"patch": {"type": "string"}}},
            description="Unified diff / proposed patch",
        )
    )
    registry.register(
        SchemaSpec(
            id="source_record.v1",
            version="1",
            kind="source_record",
            json_schema={
                "type": "object",
                "required": [
                    "source",
                    "source_type",
                    "retrieved_at",
                    "sha256",
                    "trust_label",
                ],
            },
            description="Normalized external source record",
        )
    )
    registry.register(
        SchemaSpec(
            id="connector_receipt.v1",
            version="1",
            kind="tool_receipt",
            json_schema={
                "type": "object",
                "required": [
                    "connector_id",
                    "tool_name",
                    "result_sha256",
                    "retrieved_at",
                ],
            },
            description="Durable connector invocation receipt",
        )
    )
    registry.register(
        SchemaSpec(
            id="source_capture.v1",
            version="1",
            kind="source_record",
            json_schema={
                "type": "object",
                "required": [
                    "url",
                    "sha256",
                    "media_type",
                    "bytes",
                    "retrieved_at",
                ],
            },
            description="Stored body of one retrieved external source",
        )
    )
    registry.register(
        SchemaSpec(
            id="research_ledger.v1",
            version="1",
            kind="task_output",
            json_schema={"type": "object", "required": ["entries"]},
            description="Per-run ledger of admitted search results and captures",
        )
    )
    registry.register(
        SchemaSpec(
            id="decision_record.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": ["decision_statement", "recommendation"],
            },
            description="Framed decision with its recommendation verdict",
        )
    )
    registry.register(
        SchemaSpec(
            id="option_matrix.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": ["options", "criteria", "cells"],
            },
            description="Options scored against a declared rubric",
        )
    )
    registry.register(
        SchemaSpec(
            id="change_brief.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "outcome",
                    "scope",
                    "non_goals",
                    "acceptance_criteria",
                    "constraints",
                    "risks",
                    "assumptions",
                    "unknowns",
                    "recommended_next_pack",
                ],
                "properties": {
                    "outcome": {"type": "string"},
                    "scope": {"type": "string"},
                    "non_goals": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "unknowns": {"type": "array", "items": {"type": "string"}},
                    "recommended_next_pack": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            description="Pinned change brief from change_intake framing",
        )
    )
    registry.register(
        SchemaSpec(
            id="clarification_request.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": ["questions", "blocking_unknowns", "partial_outcome"],
                "properties": {
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "blocking_unknowns": {"type": "array", "items": {"type": "string"}},
                    "partial_outcome": {"type": "string"},
                    "recommended_next_pack": {"type": ["string", "null"]},
                    "text": {"type": "string"},
                },
            },
            description="Typed clarification request from change_intake framing",
        )
    )

    for schema_id in (
        "spike_result.v1",
        "verification_report.v1",
        "release_plan.v1",
        "deployment_record.v1",
        "operational_record.v1",
    ):
        registry.register(
            SchemaSpec(
                id=schema_id,
                version="1",
                kind="reserved",
                reserved=True,
                description=f"Reserved for later phase: {schema_id}",
            )
        )
