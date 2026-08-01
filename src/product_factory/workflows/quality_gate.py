"""`quality_gate` workflow pack — multi-artifact quality reporting (P4.E).

The first pack whose land map holds more than one deliverable: a test plan, a
findings report, and (when a security task runs) a security evidence document.
All three land in one confirmed `materialize-all`, and each is validated on the
sections it must contain rather than on its filename, so a host can rename any
of them per run.

The pack reports defects, it never repairs them: `findings_are_deliverable`
tells the coordinator that a blocking finding is the product of the run and must
not spawn a repair task or fail the run.
"""

from __future__ import annotations

from product_factory.workflows.artifacts import (
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
    ROLE_VERIFICATION_REPORT,
    ArtifactLandSpec,
)
from product_factory.workflows.base import WorkflowPack

# Required headings per deliverable role. Declared next to the land specs so a
# pack owns the shape of its own documents; the validator itself is generic.
QUALITY_GATE_REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    ROLE_TEST_PLAN: (
        "Summary",
        "Risk areas",
        "Test cases",
        "Coverage gaps",
    ),
    ROLE_QUALITY_FINDINGS: (
        "Summary",
        "Findings",
        "Evidence",
        "Recommended actions",
    ),
    ROLE_SECURITY_EVIDENCE: (
        "Summary",
        "Checks performed",
        "Findings",
        "Evidence",
    ),
}

# Validator id recorded per role, so a failure names the document that failed.
QUALITY_GATE_VALIDATOR_IDS: dict[str, str] = {
    ROLE_TEST_PLAN: "test_plan_sections",
    ROLE_QUALITY_FINDINGS: "quality_findings_sections",
    ROLE_SECURITY_EVIDENCE: "security_evidence_sections",
    ROLE_VERIFICATION_REPORT: "verification_report_contract",
}

QUALITY_GATE_PACK = WorkflowPack(
    id="quality_gate",
    version="2.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string"},
            "repository_path": {"type": ["string", "null"]},
            "validation_commands": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["request_text"],
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "test_plan": {"type": "string"},
            "quality_findings": {"type": "string"},
            "security_evidence": {"type": ["string", "null"]},
            "verification_report": {"type": "object"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(
        {
            "test_design",
            "test_execution",
            "security_review",
            "independent_review",
            "composition",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "test_plan_sections",
            "quality_findings_sections",
            "security_evidence_sections",
            "secret_scan",
            "citation_presence",
            "verification_report_contract",
        ],
        "accepted_handoff_schemas": [
            "change_set.v1",
            "technical_plan.document.v1",
            "technical_plan.document.v2",
            "validation_evidence.v1",
        ],
        "accepted_handoff_roles": {
            "change_set.v1": ["change_set"],
            "technical_plan.document.v1": ["architecture_document"],
            "technical_plan.document.v2": ["architecture_document"],
            "validation_evidence.v1": ["validation_evidence"],
        },
        "accepted_handoff_states": ["approved", "evidence_complete"],
        "review": "required",
        # Registered validation commands only; the pack never runs arbitrary shell.
        "behavioral_commands": "registered",
        "write_grants": "none",
        "findings_are_deliverable": True,
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": [
            "quality.evidence-gate",
            "quality.patch-review",
            "security.threat-review",
        ],
    },
    routing_defaults={"coding_worker_tier": "mid"},
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_TEST_PLAN,
            default_logical_name="TEST_PLAN.md",
            default_dest_path="docs/TEST_PLAN.md",
            description="Risk-ranked test plan with coverage gaps.",
        ),
        ArtifactLandSpec(
            role=ROLE_QUALITY_FINDINGS,
            default_logical_name="QUALITY_FINDINGS.md",
            default_dest_path="docs/QUALITY_FINDINGS.md",
            description="Quality findings with evidence and recommended actions.",
        ),
        ArtifactLandSpec(
            role=ROLE_SECURITY_EVIDENCE,
            default_logical_name="SECURITY_EVIDENCE.md",
            default_dest_path="docs/SECURITY_EVIDENCE.md",
            # Omitted when no security task ran; materialize-all skips what a run
            # did not produce rather than landing an empty file.
            required=False,
            description="Security review checks and supporting evidence.",
        ),
        ArtifactLandSpec(
            role=ROLE_VERIFICATION_REPORT,
            default_logical_name="verification-report.json",
            default_dest_path="verification-report.json",
            media_type="application/json",
            landable=False,
            renamable=False,
            description="Typed acceptance-to-evidence verification outcome.",
        ),
    ),
    description=(
        "Read-only verification gate producing review documents and a typed "
        "acceptance-to-evidence report — reports defects without repairing them."
    ),
)
