"""Unit tests for generic `RunRequest.pack_input` validation (PM1.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.workflows import (
    WorkflowPack,
    list_workflow_packs,
    parse_pack_input_option,
    resolve_workflow_pack,
    validate_pack_input,
    validate_request_pack_input,
)

TYPED_PACK = WorkflowPack(
    id="typed_pack",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "decision_statement": {"type": "string"},
            "domain": {"type": "string"},
            "jurisdiction": {"type": ["string", "null"]},
            "seed_source_urls": {"type": "array"},
            "source_freshness_days": {"type": "integer"},
            "allow_technical_spike": {"type": "boolean"},
        },
        "required": ["decision_statement", "domain"],
        "additionalProperties": False,
    },
    output_schema={},
    allowed_capabilities=frozenset({"documentation"}),
    default_planner_mode="fixed",
    validation_policy={},
    skill_policy={},
    routing_defaults={},
)

VALID_PAYLOAD = {
    "decision_statement": "Should we adopt protocol X?",
    "domain": "payments",
    "jurisdiction": None,
    "seed_source_urls": ["https://example.test/spec"],
    "source_freshness_days": 365,
    "allow_technical_spike": False,
}


def test_valid_payload_returns_normalized_copy() -> None:
    result = validate_pack_input(TYPED_PACK, VALID_PAYLOAD)
    assert result == VALID_PAYLOAD
    assert result is not VALID_PAYLOAD


def test_missing_required_fields_fail_closed() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(TYPED_PACK, {"domain": "payments"})
    assert exc.value.details["missing"] == ["decision_statement"]
    assert exc.value.details["pack_id"] == "typed_pack"


def test_blank_required_string_counts_as_missing() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(TYPED_PACK, {"decision_statement": "  ", "domain": "payments"})
    assert exc.value.details["missing"] == ["decision_statement"]


def test_unknown_key_rejected_when_additional_properties_false() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(TYPED_PACK, {**VALID_PAYLOAD, "smuggled_tool": "apply_patch"})
    assert exc.value.details["unknown"] == ["smuggled_tool"]


def test_declared_property_types_are_enforced() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(TYPED_PACK, {**VALID_PAYLOAD, "seed_source_urls": "one-url"})
    assert [e["property"] for e in exc.value.details["type_errors"]] == ["seed_source_urls"]


def test_boolean_is_not_an_integer() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(TYPED_PACK, {**VALID_PAYLOAD, "source_freshness_days": True})
    assert exc.value.details["type_errors"][0]["expected"] == ["integer"]


def test_nullable_type_union_accepted() -> None:
    payload = {**VALID_PAYLOAD, "jurisdiction": "EU"}
    assert validate_pack_input(TYPED_PACK, payload)["jurisdiction"] == "EU"


def test_non_object_payload_rejected() -> None:
    with pytest.raises(ConfigurationError, match="must be a JSON object"):
        validate_pack_input(TYPED_PACK, ["decision_statement"])


def test_empty_payload_is_valid_for_legacy_packs() -> None:
    """Envelope-only requirements keep pre-PM1 submissions unchanged."""
    for pack in list_workflow_packs():
        required = [
            key
            for key in (pack.input_schema.get("required") or [])
            if key not in {"request_text", "repository_path", "validation_commands"}
        ]
        if required:
            # Typed packs such as feasibility_discovery fail closed on {}.
            continue
        assert validate_pack_input(pack, {}) == {}
        assert validate_pack_input(pack, None) == {}


def test_discovery_empty_payload_fails_closed() -> None:
    pack = resolve_workflow_pack("feasibility_discovery")
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(pack, {})
    assert set(exc.value.details["missing"]) == {"decision_statement", "domain"}


def test_shipped_packs_tolerate_extra_keys() -> None:
    pack = resolve_workflow_pack("technical_plan")
    assert validate_pack_input(pack, {"must_cover": "retries"})["must_cover"] == "retries"


def test_shipped_pack_still_type_checks_declared_properties() -> None:
    pack = resolve_workflow_pack("technical_plan")
    with pytest.raises(ConfigurationError):
        validate_pack_input(pack, {"must_cover": 17})


def test_request_default_pack_input_is_empty_and_validates() -> None:
    request = RunRequest(
        request_id="req-1",
        workflow_type="technical_plan",
        request_text="Design the retry policy.",
    )
    assert request.pack_input == {}
    assert validate_request_pack_input(request) == {}


def test_request_pack_input_round_trips_through_serialization() -> None:
    request = RunRequest(
        request_id="req-2",
        workflow_type="technical_plan",
        request_text="Design the retry policy.",
        pack_input={"must_cover": "idempotency"},
    )
    restored = RunRequest.model_validate(json.loads(request.model_dump_json()))
    assert restored.pack_input == {"must_cover": "idempotency"}


def test_parse_pack_input_option_inline_and_file(tmp_path: Path) -> None:
    assert parse_pack_input_option(None) == {}
    assert parse_pack_input_option("  ") == {}
    assert parse_pack_input_option('{"domain": "payments"}') == {"domain": "payments"}

    path = tmp_path / "input.json"
    path.write_text(json.dumps({"domain": "clinical"}), encoding="utf-8")
    assert parse_pack_input_option(f"@{path}") == {"domain": "clinical"}


def test_parse_pack_input_option_rejects_bad_values(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not valid JSON"):
        parse_pack_input_option("{not json")
    with pytest.raises(ConfigurationError, match="must be a JSON object"):
        parse_pack_input_option("[1, 2]")
    with pytest.raises(ConfigurationError, match="file not found"):
        parse_pack_input_option(f"@{tmp_path / 'missing.json'}")
