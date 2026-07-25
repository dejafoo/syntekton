"""Graph tests for the `quality_gate` pack (P4.E).

Covers the multi-artifact land map end to end: three deliverables from one run,
renaming any of them per request, landing them all through one confirmed
`materialize-all`, and the seeded-defect gate that proves the pack reports a
planted correctness defect instead of quietly passing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.findings import Finding
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.review_findings import score_seeded_review_detection
from product_factory.validation.pipeline import (
    validate_citations,
    validate_document_sections,
    validate_secrets,
)
from product_factory.workflows.quality_gate import (
    QUALITY_GATE_REQUIRED_SECTIONS,
    QUALITY_GATE_VALIDATOR_IDS,
)
from tests.conftest import clone_fixture

# Predeclared gate for the seeded-defect fixture below: the pack must surface the
# planted correctness defect on every run, so anything under 1/1 is a regression.
SEEDED_DETECTION_THRESHOLD = 1.0


def _coord(tmp_path: Path) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    return RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def _fixture(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    return clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def _findings(tmp_path: Path, run_id: str) -> list[Finding]:
    findings_dir = tmp_path / ".product-factory" / "runs" / run_id / "findings"
    return [
        Finding.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(findings_dir.glob("*.json"))
    ]


def _run(
    coord: RunCoordinator,
    repository: Path,
    *,
    request_id: str = "req-quality-1",
    artifact_overrides: dict[str, ArtifactOverride] | None = None,
    metadata: dict[str, str] | None = None,
):
    return coord.run(
        RunRequest(
            request_id=request_id,
            workflow_type="quality_gate",
            request_text="Assess test coverage and quality risk in the sample API.",
            repository_path=repository,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
            artifact_overrides=artifact_overrides or {},
            metadata=metadata or {},
        )
    )


def test_mock_quality_gate_produces_every_declared_deliverable(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = _run(coord, _fixture(tmp_path))

    assert manifest.final_status == "completed"
    assert manifest.metadata.get("workflow_pack_id") == "quality_gate"
    assert manifest.metadata.get("workflow_pack_version") == "1.0.0"

    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    for role, logical_name in (
        ("test_plan", "TEST_PLAN.md"),
        ("quality_findings", "QUALITY_FINDINGS.md"),
        ("security_evidence", "SECURITY_EVIDENCE.md"),
    ):
        document = (output / logical_name).read_text(encoding="utf-8")
        result = validate_document_sections(
            document,
            validator_id=QUALITY_GATE_VALIDATOR_IDS[role],
            required_sections=QUALITY_GATE_REQUIRED_SECTIONS[role],
        )
        assert result.status == "pass", result.details
        assert validate_secrets(document).status == "pass"

    findings_doc = (output / "QUALITY_FINDINGS.md").read_text(encoding="utf-8")
    assert validate_citations(findings_doc).status == "pass"

    # The plan must rank paths the run actually inspected, not a generic default.
    test_plan = (output / "TEST_PLAN.md").read_text(encoding="utf-8")
    assert "`src/app/main.py`" in test_plan
    assert "`tests/test_main.py`" in test_plan


def test_quality_gate_never_receives_repository_write_tools(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = _run(coord, _fixture(tmp_path))

    tool_names = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
    assert "create_file" not in tool_names
    assert "apply_patch" not in tool_names


def test_quality_gate_honors_requested_deliverable_names(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = _run(
        coord,
        _fixture(tmp_path),
        artifact_overrides={
            "test_plan": ArtifactOverride(dest_path="docs/qa/integration_test_plan.md"),
            "quality_findings": ArtifactOverride(
                logical_name="release_readiness.md",
                dest_path="docs/qa/release_readiness.md",
            ),
        },
    )

    assert manifest.final_status == "completed"
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    assert (output / "integration_test_plan.md").exists()
    assert (output / "release_readiness.md").exists()
    assert not (output / "TEST_PLAN.md").exists()
    assert not (output / "QUALITY_FINDINGS.md").exists()
    # An un-overridden role keeps its pack default.
    assert (output / "SECURITY_EVIDENCE.md").exists()

    # The H1 follows the resolved name, and section validation is unaffected by it.
    plan_doc = (output / "integration_test_plan.md").read_text(encoding="utf-8")
    assert plan_doc.startswith("# integration_test_plan.md")
    assert (
        validate_document_sections(
            plan_doc,
            validator_id=QUALITY_GATE_VALIDATOR_IDS["test_plan"],
            required_sections=QUALITY_GATE_REQUIRED_SECTIONS["test_plan"],
        ).status
        == "pass"
    )


def test_materialize_all_lands_every_quality_deliverable(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    repository = _fixture(tmp_path)
    service = HostService(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    submitted = service.submit(
        RunRequest(
            request_id="req-quality-land",
            workflow_type="quality_gate",
            request_text="Assess test coverage and quality risk in the sample API.",
            repository_path=repository,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
            artifact_overrides={
                "test_plan": ArtifactOverride(dest_path="docs/qa/integration_test_plan.md")
            },
        ),
        mock=True,
        detach=False,
    )
    assert submitted.ok, submitted.model_dump()
    run_id = submitted.run_id
    assert run_id is not None

    response = service.materialize_all(run_id)
    assert response.ok, response.model_dump()
    assert response.data is not None

    landed_roles = {entry["role"] for entry in response.data["landed"]}
    assert landed_roles == {"test_plan", "quality_findings", "security_evidence"}
    assert (repository / "docs" / "qa" / "integration_test_plan.md").is_file()
    assert (repository / "docs" / "QUALITY_FINDINGS.md").is_file()
    assert (repository / "docs" / "SECURITY_EVIDENCE.md").is_file()

    # Landing is audited per file, not once per batch.
    events = service.list_events(run_id, after_seq=0, limit=500)
    materialized = [event for event in events if event.get("type") == "artifact.materialized"]
    assert len(materialized) == 3


def test_seeded_correctness_defect_is_reported_not_repaired(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    seeded_path = "src/app/main.py"
    manifest = _run(
        coord,
        _fixture(tmp_path),
        metadata={
            "seed_review_paths": seeded_path,
            "seed_review_expect_blocking": "true",
        },
    )

    # A reported defect is the deliverable: the run completes and never repairs.
    assert manifest.final_status == "completed"
    assert manifest.repair_count == 0

    blocking = [
        finding
        for finding in _findings(tmp_path, manifest.run_id)
        if finding.severity == "blocking"
    ]
    assert blocking, "seeded defect produced no blocking finding"

    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    findings_doc = (output / "QUALITY_FINDINGS.md").read_text(encoding="utf-8")
    assert seeded_path in findings_doc
    assert "Blocking findings: 1" in findings_doc
    assert validate_citations(findings_doc).status == "pass"


def test_seeded_detection_rate_meets_predeclared_threshold(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    seeded_path = "src/app/main.py"
    manifest = _run(
        coord,
        _fixture(tmp_path),
        metadata={
            "seed_review_paths": seeded_path,
            "seed_review_expect_blocking": "true",
        },
    )
    score = score_seeded_review_detection(
        _findings(tmp_path, manifest.run_id),
        seeded_paths=[seeded_path],
        expect_blocking=True,
    )
    assert score["detected"] is True
    detected = 1 if score["detected"] else 0
    assert detected / 1 >= SEEDED_DETECTION_THRESHOLD


def test_clean_repository_yields_no_blocking_findings(tmp_path: Path) -> None:
    """False-positive guard: a non-defect note must not be reported as blocking."""
    coord = _coord(tmp_path)
    manifest = _run(
        coord,
        _fixture(tmp_path),
        metadata={
            "seed_review_paths": "src/app/main.py",
            "seed_review_expect_blocking": "false",
        },
    )
    assert manifest.final_status == "completed"
    blocking = [
        finding
        for finding in _findings(tmp_path, manifest.run_id)
        if finding.severity == "blocking"
    ]
    assert not blocking

    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    findings_doc = (output / "QUALITY_FINDINGS.md").read_text(encoding="utf-8")
    assert "Blocking findings: 0" in findings_doc
