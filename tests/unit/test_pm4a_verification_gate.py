from __future__ import annotations

import json
from pathlib import Path

from product_factory.domain.runs import RunRequest
from product_factory.validation.pipeline import validate_verification_report
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.handlers.quality_gate import QualityGateHandler
from product_factory.workflows.quality_gate import QUALITY_GATE_PACK

FIXTURES = Path(__file__).parents[1] / "fixtures" / "verification"


def _context(pack_input: dict) -> ComposeContext:
    return ComposeContext(
        request=RunRequest(
            request_id="pm4a-verify",
            workflow_type="quality_gate",
            request_text="Verify the proposed change.",
            pack_input=pack_input,
        ),
        role="verification_report",
        document_name="verification-report.json",
        profile="quality_gate.v2",
    )


def test_verification_report_maps_acceptance_to_evidence() -> None:
    document = QualityGateHandler().compose(
        "verification_report",
        _context(
            {
                "acceptance_refs": ["plan:a#AC-001"],
                "evidence_refs": ["validation_evidence.v1:b"],
                "validator_results": [{"validator_id": "pytest", "status": "pass"}],
            }
        ),
    )
    payload = json.loads(document)

    assert payload["outcome"] == "passes"
    assert payload["acceptance_results"] == [
        {
            "acceptance_ref": "plan:a#AC-001",
            "evidence_refs": ["validation_evidence.v1:b"],
            "status": "pass",
        }
    ]
    assert validate_verification_report(document).status == "pass"


def test_skipped_registered_validator_is_insufficient_evidence() -> None:
    document = QualityGateHandler().compose(
        "verification_report",
        _context(
            {
                "acceptance_refs": ["plan:a#AC-001"],
                "evidence_refs": ["validation_evidence.v1:b"],
                "validator_results": [{"validator_id": "pytest", "status": "skipped"}],
            }
        ),
    )
    payload = json.loads(document)
    assert payload["outcome"] == "insufficient_evidence"
    assert payload["acceptance_results"][0]["status"] == "gap"


def test_runtime_validation_evidence_is_consumed() -> None:
    ctx = _context({"acceptance_refs": ["plan:a#AC-001"]})
    ctx.validation_evidence_refs = ["e" * 64]
    ctx.validator_results = [{"validator_id": "behavioral:python_tests", "status": "pass"}]

    payload = json.loads(QualityGateHandler().compose("verification_report", ctx))

    assert payload["outcome"] == "passes"
    assert payload["evidence_refs"] == ["e" * 64]


def test_verification_fixtures_are_machine_validated() -> None:
    for name in ("passes.json", "insufficient-evidence.json"):
        document = (FIXTURES / name).read_text(encoding="utf-8")
        assert validate_verification_report(document).status == "pass"


def test_quality_gate_v2_keeps_no_repair_authority() -> None:
    assert QUALITY_GATE_PACK.version == "2.0.0"
    assert QUALITY_GATE_PACK.validation_policy["write_grants"] == "none"
    assert QUALITY_GATE_PACK.validation_policy["findings_are_deliverable"] is True
    assert QualityGateHandler().authority_class() == "read_only"
