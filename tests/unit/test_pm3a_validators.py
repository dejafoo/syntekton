"""PM3.A investigation and technical-plan v2 validator tests."""

from __future__ import annotations

from pathlib import Path

from product_factory.validation.pipeline import (
    validate_acceptance_verification_links,
    validate_investigation_document,
    validate_investigation_provenance,
    validate_no_invented_defaults,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def test_investigation_v2_requires_labels_and_fact_provenance() -> None:
    valid = _fixture("investigation/valid_evidence_report.md")
    assert validate_investigation_document(valid).status == "pass"
    assert validate_investigation_provenance(valid).status == "pass"

    unsupported = validate_investigation_provenance(_fixture("investigation/unsupported_fact.md"))
    assert unsupported.status == "fail"
    assert unsupported.details["unsupported_facts"]


def test_technical_plan_links_every_acceptance_to_verification() -> None:
    valid = validate_acceptance_verification_links(_fixture("plan/valid_technical_plan.md"))
    assert valid.status == "pass"

    missing = validate_acceptance_verification_links(_fixture("plan/missing_verification_link.md"))
    assert missing.status == "fail"
    assert missing.details["missing_verification"] == ["002"]


def test_technical_plan_escalates_unknowns_instead_of_inventing_defaults() -> None:
    assert validate_no_invented_defaults(_fixture("plan/valid_technical_plan.md")).status == "pass"
    invented = validate_no_invented_defaults(_fixture("plan/invented_default.md"))
    assert invented.status == "fail"
    assert invented.details["invented_defaults"]
