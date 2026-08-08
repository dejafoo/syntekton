"""PM1.A skill-eval harness: disable_skills, discovery cases, ablation subjects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.evaluation.bench import BenchmarkRunner
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import run_deterministic_checks
from product_factory.evaluation.loader import load_eval_cases
from product_factory.evaluation.subjects import SubjectArtifact
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def _discovery_case(**updates) -> EvalCase:
    base = EvalCase(
        id="feas_test",
        workflow_type="feasibility_discovery",
        request="Assess integration feasibility.",
        must_cover=["webhook"],
        expected_source_classes=["vendor_api"],
    )
    return base.model_copy(update=updates)


def _good_dossier() -> str:
    return """# FEASIBILITY_DISCOVERY.md

## Decision
Assess webhook delivery guarantees.

## Scope
Public docs only.

## Domain model
Vendor webhook boundary.

## Options
- Option A: rely on vendor retries
- Option B: add idempotent inbox

## Comparison rubric
Capability, reliability, operational burden.

## Evidence
- fact: Vendor API states signed payloads (source_id: src-vendor-1, https://example.com/api).
- inference: Retries are likely at-least-once based on secondary commentary.
- unknown: Exact replay window.

## Assumptions
- Operator will not use production credentials.

## Unknowns
- Contractual SLA.

## Risks
- Averaging conflicting sources.

## Constraints
- Read-only discovery.

## Recommendation
insufficient_evidence

## Next step
Obtain a current primary source before planning.
"""


def test_loader_rejects_discovery_without_anchors(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text(
        "id: bad\nworkflow_type: feasibility_discovery\nrequest: decide something\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must_cover or expected_source_classes"):
        load_eval_cases(tmp_path)


def test_feasibility_fixtures_load() -> None:
    root = Path(__file__).resolve().parents[2]
    cases = load_eval_cases(root / "tests" / "eval_cases")
    feas = [c for c in cases if c.workflow_type == "feasibility_discovery"]
    ids = {c.id for c in feas}
    required = {
        "feas_unfamiliar_integration",
        "feas_conflicting_sources",
        "feas_incomplete_jurisdiction",
        "feas_stale_vendor",
        "feas_insufficient_evidence",
    }
    assert required <= ids
    assert {"sd6_discovery_sparse_evidence", "sd6_discovery_jurisdiction_gap"} <= ids
    assert all(c.must_cover or c.expected_source_classes for c in feas)


def test_discovery_deterministic_checks_pass_and_fail() -> None:
    case = _discovery_case(metadata={"min_cited_sources": 1})
    ok = run_deterministic_checks(
        case,
        SubjectArtifact(
            subject_id="orchestration_with_skills",
            case_id=case.id,
            status="completed",
            artifact_text=_good_dossier(),
            artifact_kind="other",
        ),
    )
    assert all(r.status == "pass" for r in ok if r.validator_id.startswith("feasibility_"))

    bad = run_deterministic_checks(
        case,
        SubjectArtifact(
            subject_id="orchestration_with_skills",
            case_id=case.id,
            status="completed",
            artifact_text="# Incomplete\n\n## Decision\nOnly a decision.\n",
            artifact_kind="other",
        ),
    )
    assert any(r.validator_id == "feasibility_sections" and r.status == "fail" for r in bad)

    unsupported = _good_dossier().replace(
        "- fact: Vendor API states signed payloads (source_id: src-vendor-1, https://example.com/api).",
        "- fact: Vendor API states signed payloads without any citation.",
    )
    unsupported_results = run_deterministic_checks(
        case,
        SubjectArtifact(
            subject_id="orchestration_with_skills",
            case_id=case.id,
            status="completed",
            artifact_text=unsupported,
            artifact_kind="other",
        ),
    )
    assert any(
        r.validator_id == "feasibility_unsupported_claims" and r.status == "fail"
        for r in unsupported_results
    )


def test_architecture_and_code_checks_still_work() -> None:
    arch = EvalCase(
        id="arch",
        workflow_type="architecture",
        request="Design a cache",
        must_cover=["cache"],
    )
    arch_results = run_deterministic_checks(
        arch,
        SubjectArtifact(
            subject_id="full_orchestration",
            case_id="arch",
            status="completed",
            artifact_text="not a real architecture",
            artifact_kind="architecture",
        ),
    )
    assert any(
        r.validator_id == "architecture_sections" and r.status == "fail" for r in arch_results
    )

    code = EvalCase(
        id="code",
        workflow_type="code_change",
        request="Change code",
        smoke_commands=["python_tests"],
    )
    code_results = run_deterministic_checks(
        code,
        SubjectArtifact(
            subject_id="full_orchestration",
            case_id="code",
            status="completed",
            artifact_text="I changed the code.",
            artifact_kind="patch",
        ),
    )
    assert any(r.validator_id == "patch_format" and r.status == "fail" for r in code_results)


def test_skill_ablation_subjects_registered() -> None:
    root = Path(__file__).resolve().parents[2]
    runner = BenchmarkRunner(
        app_config=load_config(root),
        gateway=MockGateway(),
        use_deterministic_planner=True,
    )
    assert "orchestration_with_skills" in runner._runners
    assert "orchestration_no_skills" in runner._runners
    assert runner._runners["orchestration_no_skills"].metadata.get("disable_skills") is True
    assert runner._runners["orchestration_with_skills"].metadata.get("disable_skills") is False


def test_disable_skills_records_omitted_context(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    coord = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coord.run(
        RunRequest(
            request_id="skills-off",
            workflow_type="architecture",
            request_text="Design a tiny offline-first notes sync service.",
            approval_policy="none",
            metadata={
                "disable_skills": "true",
                "planner_mode": "fixed",
                "disable_review": "true",
            },
        )
    )
    prompts = tmp_path / ".product-factory" / "runs" / manifest.run_id / "prompts"
    manifests = list(prompts.glob("*.manifest.json"))
    assert manifests
    omitted_hits = 0
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        omitted = payload.get("omitted_context") or []
        if "skills_disabled" in omitted:
            omitted_hits += 1
            assert payload.get("selected_skill_versions") in ({}, None)
    assert omitted_hits >= 1
