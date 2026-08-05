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
    "evidence_report.v2": "evidence_report.document.v2",
    "technical_plan.v2": "technical_plan.document.v2",
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
    "spike_result.document.v1": "spike_result.v1",
    "technical_spike.v1": "spike_result.v1",
    # PM4 change-intelligence outputs — canonical bare ids are preferred, while
    # document-style spellings remain accepted at planner boundaries.
    "change_set.document.v1": "change_set.v1",
    "verification_report.document.v1": "verification_report.v1",
    "validation_evidence.document.v1": "validation_evidence.v1",
    # PM5 release/operations outputs — document-style spellings remain accepted
    # at planner boundaries while canonical ids stay compact.
    "release_plan.document.v1": "release_plan.v1",
    "deployment_record.document.v1": "deployment_record.v1",
    "operational_record.document.v1": "operational_record.v1",
}

ROLE_TO_SCHEMA: dict[str, str] = {
    "evidence_report": "evidence_report.document.v2",
    "architecture_document": "technical_plan.document.v2",
    "proposed_patch": "change_set.patch.v1",
    "test_plan": "test_plan.document.v1",
    "quality_findings": "quality_findings.document.v1",
    "security_evidence": "security_evidence.document.v1",
    "feasibility_dossier": "feasibility_dossier.v1",
    "change_brief": "change_brief.v1",
    "clarification_request": "clarification_request.v1",
    "spike_result": "spike_result.v1",
    "change_set": "change_set.v1",
    "verification_report": "verification_report.v1",
    "validation_evidence": "validation_evidence.v1",
    "release_plan": "release_plan.v1",
    "deployment_record": "deployment_record.v1",
    "operational_record": "operational_record.v1",
}

# Typed PM5 outputs are registered for fixture and handoff validation in Phase
# 0, but a workflow may not compile a writer until its pack declares the named
# contract validator. Later phases satisfy these gates without coordinator
# branches.
OUTPUT_SCHEMA_VALIDATOR_IDS: dict[str, str] = {
    "release_plan.v1": "release_plan_contract",
    "deployment_record.v1": "deployment_record_contract",
    "operational_record.v1": "operational_record_contract",
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
            id="evidence_report.document.v2",
            version="2",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "repository_revision",
                    "retrieval_window",
                    "evidence",
                    "handoff_refs",
                ],
                "properties": {
                    "repository_revision": {"type": "string"},
                    "retrieval_window": {"type": "object"},
                    "evidence": {"type": "array"},
                    "handoff_refs": {"type": "array"},
                    "text": {"type": "string"},
                },
            },
            description=(
                "Pinned repository evidence report with fact/inference/unknown "
                "labels and source provenance"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="technical_plan.document.v2",
            version="2",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "handoff_refs",
                    "acceptance_criteria",
                    "implementation_slices",
                    "verification_evidence",
                    "approval_items",
                ],
                "properties": {
                    "handoff_refs": {"type": "array"},
                    "acceptance_criteria": {"type": "array"},
                    "implementation_slices": {"type": "array"},
                    "verification_evidence": {"type": "array"},
                    "approval_items": {"type": "array"},
                    "text": {"type": "string"},
                },
            },
            description=(
                "Pinned technical plan linking acceptance criteria, implementation "
                "slices, verification evidence, and approval items"
            ),
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
            id="change_set.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "base_revision",
                    "patch_sha256",
                    "artifact_hashes",
                    "changed_paths",
                    "acceptance_refs",
                    "validation_evidence_refs",
                    "producer_run_id",
                ],
                "properties": {
                    "base_revision": {"type": "string", "minLength": 1},
                    "patch_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "artifact_hashes": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "changed_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "acceptance_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "validation_evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "producer_run_id": {"type": "string", "minLength": 1},
                },
                "additionalProperties": True,
            },
            description=(
                "Content-addressed repository change summary linking the patch, "
                "changed paths, acceptance criteria, and validation evidence"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="verification_report.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "outcome",
                    "acceptance_results",
                    "validator_profile_id",
                    "evidence_refs",
                    "residual_risk",
                ],
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": [
                            "passes",
                            "passes_with_risk",
                            "blocked",
                            "insufficient_evidence",
                        ],
                    },
                    "acceptance_results": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "validator_profile_id": {"type": "string", "minLength": 1},
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "residual_risk": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "additionalProperties": True,
            },
            description=(
                "Typed verification outcome with acceptance-level results, "
                "evidence references, validator profile, and residual risk"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="validation_evidence.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "profile_version",
                    "command_id",
                    "receipt",
                    "input_revision",
                    "normalized_outcomes",
                    "raw_ref",
                    "baseline_comparison",
                ],
                "properties": {
                    "profile_version": {"type": "string", "minLength": 1},
                    "command_id": {"type": "string", "minLength": 1},
                    "receipt": {"type": "object"},
                    "input_revision": {"type": "string", "minLength": 1},
                    "normalized_outcomes": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "raw_ref": {"type": "string", "minLength": 1},
                    "baseline_comparison": {"type": "object"},
                },
                "additionalProperties": True,
            },
            description=(
                "Normalized result of one registered validation command with "
                "receipt, raw evidence reference, and baseline comparison"
            ),
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
    registry.register(
        SchemaSpec(
            id="spike_result.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "hypothesis",
                    "method",
                    "measurements",
                    "limits",
                    "artifact_refs",
                ],
                "properties": {
                    "schema_id": {"const": "spike_result.v1"},
                    "hypothesis": {"type": "string", "minLength": 1},
                    "method": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "object", "minProperties": 1},
                        ]
                    },
                    "measurements": {"type": "object"},
                    "limits": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "findings": {"type": "array"},
                    "artifact_refs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["role", "sha256", "schema_id"],
                        },
                        "minItems": 3,
                    },
                },
                "additionalProperties": True,
            },
            description=(
                "Confined technical-spike result with hypothesis, method, "
                "measurements, and explicit limits"
            ),
        )
    )

    for schema_id, description in (
        ("contract_inventory.v1", "Typed interface contract inventory"),
        ("contract_compatibility.v1", "Typed contract compatibility comparison"),
        ("contract_simulation.v1", "Synthetic fixture simulation evidence"),
    ):
        registry.register(
            SchemaSpec(
                id=schema_id,
                version="1",
                kind="tool_receipt",
                json_schema={
                    "type": "object",
                    "required": ["schema_id", "role", "result"],
                    "properties": {
                        "schema_id": {"const": schema_id},
                        "role": {"type": "string"},
                        "result": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
                description=description,
            )
        )

    registry.register(
        SchemaSpec(
            id="effective_task_policy.v1",
            version="1",
            kind="profile",
            json_schema={
                "type": "object",
                "required": [
                    "schema_version",
                    "task_id",
                    "run_id",
                    "capability",
                    "allowed_tool_names",
                    "prompt_tool_names",
                    "primary_model_profile",
                ],
                "properties": {
                    "schema_version": {"const": "effective_task_policy.v1"},
                    "task_id": {"type": "string", "minLength": 1},
                    "run_id": {"type": "string", "minLength": 1},
                    "pack_id": {"type": ["string", "null"]},
                    "pack_version": {"type": ["string", "null"]},
                    "capability": {"type": "string", "minLength": 1},
                    "executor_mode": {"type": "string"},
                    "allowed_tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "allowed_tool_classes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "connector_decisions": {"type": "object"},
                    "path_scopes": {"type": "object"},
                    "call_limits": {"type": "object"},
                    "result_limits": {"type": "object"},
                    "data_classification": {"type": "string"},
                    "prompt_tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "prompt_reduction_reason": {"type": ["string", "null"]},
                    "skill_ids": {"type": "array", "items": {"type": "string"}},
                    "profile_ids": {"type": "array", "items": {"type": "string"}},
                    "reference_pack_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "stack_profile_artifact_sha256": {"type": ["string", "null"]},
                    "stack_profile_digest": {"type": ["string", "null"]},
                    "stack_profile_schema_version": {"type": ["string", "null"]},
                    "route_class": {"type": "string"},
                    "primary_model_profile": {"type": "string", "minLength": 1},
                    "fallback_model_profile": {"type": ["string", "null"]},
                    "fallback_eligible": {"type": "boolean"},
                    "budget_ceiling": {"type": "object"},
                    "validator_ids": {"type": "array", "items": {"type": "string"}},
                    "repair_eligible": {"type": "boolean"},
                    "approval_required": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            description=(
                "Immutable effective task policy resolved before context assembly (ADR-007 / RF2)"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="local_route_admission.v1",
            version="1",
            kind="profile",
            json_schema={
                "type": "object",
                "required": ["schema_version", "profile", "model", "report"],
                "properties": {
                    "schema_version": {
                        "type": "string",
                        "const": "local_route_admission.v1",
                    },
                    "profile": {"type": "string", "minLength": 1},
                    "model": {"type": "string"},
                    "route_class": {"type": "string"},
                    "breaker": {"type": "object"},
                    "report": {"type": "object"},
                    "admission": {"type": ["object", "null"]},
                    "fallback": {"type": "object"},
                },
                "additionalProperties": True,
            },
            description="Measured local-route probe and admission evidence (RF5)",
        )
    )

    registry.register(
        SchemaSpec(
            id="release_plan.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "outcome",
                    "input_digests",
                    "version",
                    "change_notes",
                    "compatibility_impact",
                    "migration_preconditions",
                    "rollout_phases",
                    "monitors",
                    "rollback_criteria",
                    "required_approvals",
                ],
                "properties": {
                    "schema_id": {"const": "release_plan.v1"},
                    "outcome": {
                        "type": "string",
                        "enum": ["ready", "blocked", "needs_decision"],
                    },
                    "input_digests": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{64}$",
                        },
                    },
                    "version": {"type": "string", "minLength": 1},
                    "change_notes": {"type": "array", "items": {"type": "string"}},
                    "compatibility_impact": {"type": "array", "items": {"type": "string"}},
                    "migration_preconditions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "rollout_phases": {"type": "array", "items": {"type": "object"}},
                    "monitors": {"type": "array", "items": {"type": "object"}},
                    "rollback_criteria": {"type": "array", "items": {"type": "object"}},
                    "required_approvals": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
            description=(
                "Pinned release decision with rollout, monitoring, rollback, "
                "migration, and approval evidence"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="deployment_record.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "release_plan_digest",
                    "artifact_digest",
                    "target_id",
                    "environment",
                    "outcome",
                    "action_log",
                    "health_checks",
                    "observed_metrics",
                    "policy_decisions",
                    "rollback_result",
                ],
                "properties": {
                    "schema_id": {"const": "deployment_record.v1"},
                    "release_plan_digest": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "artifact_digest": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "target_id": {"type": "string", "minLength": 1},
                    "environment": {"type": "string", "minLength": 1},
                    "outcome": {
                        "type": "string",
                        "enum": ["succeeded", "failed", "halted", "rolled_back", "unknown"],
                    },
                    "action_log": {"type": "array", "items": {"type": "object"}},
                    "health_checks": {"type": "array", "items": {"type": "object"}},
                    "observed_metrics": {"type": "array", "items": {"type": "object"}},
                    "policy_decisions": {"type": "array", "items": {"type": "object"}},
                    "rollback_result": {"type": ["object", "null"]},
                },
                "additionalProperties": True,
            },
            description=(
                "Durable deployment receipt binding an approved release and immutable "
                "artifact to target actions, health evidence, and rollback outcome"
            ),
        )
    )
    registry.register(
        SchemaSpec(
            id="operational_record.v1",
            version="1",
            kind="task_output",
            json_schema={
                "type": "object",
                "required": [
                    "record_type",
                    "service_id",
                    "environment",
                    "time_window",
                    "impact",
                    "timeline",
                    "evidence",
                    "hypotheses",
                    "recommendations",
                    "follow_up",
                    "follow_up_action",
                    "authority",
                ],
                "properties": {
                    "schema_id": {"const": "operational_record.v1"},
                    "record_type": {
                        "type": "string",
                        "enum": ["incident_triage", "service_health_review"],
                    },
                    "service_id": {"type": "string", "minLength": 1},
                    "environment": {"type": "string", "minLength": 1},
                    "time_window": {"type": "object"},
                    "impact": {"type": "object"},
                    "timeline": {"type": "array", "items": {"type": "object"}},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                    "hypotheses": {"type": "array", "items": {"type": "object"}},
                    "recommendations": {"type": "array", "items": {"type": "string"}},
                    "follow_up": {
                        "type": "string",
                        "enum": [
                            "change_intake",
                            "repository_investigation",
                            "rollback_decision",
                            "human_escalation",
                            "none",
                        ],
                    },
                    "follow_up_action": {
                        "type": "object",
                        "required": ["type", "reason", "requires_human"],
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "change_intake",
                                    "repository_investigation",
                                    "rollback_decision",
                                    "human_escalation",
                                    "none",
                                ],
                            },
                            "reason": {"type": "string", "minLength": 1},
                            "requires_human": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                    "authority": {
                        "type": "object",
                        "properties": {
                            "class": {"const": "external_read"},
                            "deploy": {"const": False},
                            "restart": {"const": False},
                            "traffic_mutation": {"const": False},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": True,
            },
            description=(
                "Bounded operational analysis separating observed evidence from "
                "hypotheses and naming an explicit follow-up"
            ),
        )
    )
