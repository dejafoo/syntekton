"""Deterministic fixed planner templates for pack handlers (PM0.A)."""

from __future__ import annotations

from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ROLE_EVIDENCE_REPORT,
    ROLE_FEASIBILITY_DOSSIER,
    ROLE_PROPOSED_PATCH,
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
)

def default_code_change_plan(request_text: str) -> PlannerOutput:
    """Risk-aware deterministic plan used by offline tests."""
    proposal = PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Inspect repository structure",
                capability="repository_analysis",
                objective="Identify relevant modules and conventions",
                expected_output_schema="repository_analysis.v1",
                required_skills=["repository-inspection"],
                required_tool_classes={"repository_read"},
                prohibited_actions={"file_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Relevant files identified",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Implement change",
                capability="implementation",
                objective=request_text,
                dependencies=["T-001"],
                expected_output_schema="implementation_result.v1",
                required_tool_classes={
                    "repository_read",
                    "repository_write",
                    "git_read",
                    "git_write",
                },
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Change implemented with tests",
                        verification="test_suite",
                    )
                ],
                allowed_path_patterns=["**/*"],
                rationale="Justified broad path scope for fixture-wide code changes",
            ),
            TaskSpec(
                id="T-003",
                title="Independent review",
                capability="independent_review",
                objective="Review the proposed patch",
                dependencies=["T-002"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read", "git_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="Findings cite evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Compose patch",
                capability="composition",
                objective="Produce final proposed.patch",
                dependencies=["T-002", "T-003"],
                expected_output_schema="composition_result.v1",
                required_tool_classes={"git_read", "artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Patch artifact produced",
                        verification="artifact_check",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="proposed.patch",
                composer_task_id="T-004",
                role=ROLE_PROPOSED_PATCH,
            )
        ],
        validation_strategy="deterministic then independent review",
        risk_classification="low",
    )
    risk_terms = {
        "auth",
        "security",
        "permission",
        "secret",
        "migration",
        "database",
        "payment",
        "concurrency",
        "encryption",
    }
    high_risk = any(term in request_text.lower() for term in risk_terms)
    if not high_risk:
        implementation = proposal.tasks[1].model_copy(update={"dependencies": []})
        composition = proposal.tasks[3].model_copy(update={"dependencies": [implementation.id]})
        proposal = proposal.model_copy(
            update={
                "tasks": [implementation, composition],
                "validation_strategy": "deterministic behavioral validation",
                "risk_classification": "low",
            }
        )
    else:
        proposal = proposal.model_copy(update={"risk_classification": "high"})
    return proposal


def default_architecture_plan(request_text: str) -> PlannerOutput:
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Gather requirements",
                capability="requirements",
                objective="Clarify requirements and assumptions",
                expected_output_schema="requirements.v1",
                required_tool_classes={"repository_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Requirements captured",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Draft architecture",
                capability="architecture",
                objective="Produce architecture sections",
                dependencies=["T-001"],
                expected_output_schema="architecture_partial.v1",
                required_tool_classes={"artifact_write", "web_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Architecture draft created",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-003",
                title="Compose ARCHITECTURE.md",
                capability="composition",
                objective="Compose final architecture document",
                dependencies=["T-002"],
                expected_output_schema="architecture_doc.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="ARCHITECTURE.md complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Independent review",
                capability="independent_review",
                objective="Review architecture for gaps",
                dependencies=["T-003"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Review complete",
                        verification="evidence_check",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="ARCHITECTURE.md",
                composer_task_id="T-003",
                role=ROLE_ARCHITECTURE_DOCUMENT,
            )
        ],
        validation_strategy="section checks then review",
        risk_classification="low",
    )


def default_technical_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for `technical_plan` — same shape as architecture."""
    return default_architecture_plan(request_text)


def default_investigation_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for read-only repository investigation (P3.D)."""
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Inspect repository structure",
                capability="repository_analysis",
                objective="Identify relevant modules, evidence paths, and conventions",
                expected_output_schema="repository_analysis.v1",
                required_skills=["repository-inspection"],
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Relevant files identified with path evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Compose evidence report",
                capability="composition",
                objective="Produce EVIDENCE_REPORT.md with cited paths and assumptions",
                dependencies=["T-001"],
                expected_output_schema="evidence_report.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Evidence report with citations and assumptions",
                        verification="static_rule",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="EVIDENCE_REPORT.md",
                composer_task_id="T-002",
                role=ROLE_EVIDENCE_REPORT,
            )
        ],
        validation_strategy="section checks, citation presence, secret scan",
        risk_classification="low",
    )


def default_quality_gate_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for the `quality_gate` pack (P4.E).

    Three composer tasks, one per land-map role, so each deliverable has a single
    owning task that the coordinator can resolve back to its role. No task may
    write to the repository: the pack reports, it does not change code.
    """
    read_only = {"file_write", "repository_write", "git_write"}
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Design quality checks",
                capability="test_design",
                objective="Identify risk areas and the checks that would cover them",
                expected_output_schema="test_design.v1",
                required_tool_classes={"repository_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Risk areas identified with paths",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Execute registered validation commands",
                capability="test_execution",
                objective="Run the registered validation commands and capture results",
                dependencies=["T-001"],
                expected_output_schema="test_execution.v1",
                required_tool_classes={"repository_read", "validation_command"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Command outcomes captured or explicitly skipped",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-003",
                title="Security review",
                capability="security_review",
                objective="Review the repository for security-relevant defects",
                dependencies=["T-001"],
                expected_output_schema="security_review.v1",
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="Security checks recorded with evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Independent review",
                capability="independent_review",
                objective="Independently review quality with cited evidence",
                dependencies=["T-002", "T-003"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Findings cite file evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-005",
                title="Compose test plan",
                capability="composition",
                objective="Compose the test plan deliverable",
                dependencies=["T-001"],
                expected_output_schema="test_plan.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-005",
                        description="Test plan sections complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-006",
                title="Compose security evidence",
                capability="composition",
                objective="Compose the security evidence deliverable",
                dependencies=["T-003"],
                expected_output_schema="security_evidence.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-006",
                        description="Security evidence sections complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-007",
                title="Compose quality findings",
                capability="composition",
                objective="Compose the quality findings deliverable",
                dependencies=["T-004", "T-005", "T-006"],
                expected_output_schema="quality_findings.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-007",
                        description="Findings carry evidence and recommended actions",
                        verification="static_rule",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="TEST_PLAN.md",
                composer_task_id="T-005",
                role=ROLE_TEST_PLAN,
            ),
            FinalArtifactSpec(
                logical_name="SECURITY_EVIDENCE.md",
                composer_task_id="T-006",
                role=ROLE_SECURITY_EVIDENCE,
            ),
            FinalArtifactSpec(
                logical_name="QUALITY_FINDINGS.md",
                composer_task_id="T-007",
                role=ROLE_QUALITY_FINDINGS,
            ),
        ],
        validation_strategy="section checks, citation presence, secret scan",
        risk_classification="low",
    )


def default_feasibility_discovery_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for bounded public-evidence discovery (PM1.D / WF1)."""
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Frame the decision",
                capability="decision_analysis",
                objective="Clarify the decision statement, options skeleton, and comparison rubric",
                expected_output_schema="decision_record.v1",
                required_skills=["discovery.option-framing"],
                required_tool_classes={"artifact_write", "evidence_build"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Decision framed with at least two options and a declared rubric",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Retrieve allowed evidence",
                capability="domain_research",
                objective="Gather public or approved sources within the active source policy",
                dependencies=["T-001"],
                expected_output_schema="research_ledger.v1",
                required_skills=["discovery.evidence-assessment"],
                required_tool_classes={
                    "repository_read",
                    "artifact_write",
                    "web_read",
                    "source_read",
                    "evidence_build",
                },
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Evidence captured with source class and freshness metadata",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-003",
                title="Normalize options and constraints",
                capability="decision_analysis",
                objective="Score options against the rubric; keep unknown cells unknown",
                dependencies=["T-002"],
                expected_output_schema="option_matrix.v1",
                required_skills=["discovery.option-framing"],
                required_tool_classes={"artifact_write", "evidence_build"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="Option matrix covers every criterion with explicit unknowns",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Independent evidence review",
                capability="independent_review",
                objective="Challenge unsupported claims, conflicts, staleness, and regulated conclusions",
                dependencies=["T-003"],
                expected_output_schema="review_findings.v1",
                required_skills=["discovery.evidence-assessment"],
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Review findings cite evidence and escalate regulated claims",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-005",
                title="Compose feasibility dossier",
                capability="composition",
                objective="Produce FEASIBILITY_DISCOVERY.md with labeled provenance and next step",
                dependencies=["T-004"],
                expected_output_schema="feasibility_dossier.v1",
                required_skills=["discovery.option-framing"],
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-005",
                        description="Dossier sections complete with recommendation enum",
                        verification="static_rule",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="FEASIBILITY_DISCOVERY.md",
                composer_task_id="T-005",
                role=ROLE_FEASIBILITY_DOSSIER,
            )
        ],
        validation_strategy=(
            "feasibility sections, research provenance, option comparison, "
            "regulated claims review, secret scan"
        ),
        risk_classification="low",
    )
