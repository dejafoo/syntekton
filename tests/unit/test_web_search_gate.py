"""Web-search citation expectations and connector enablement."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.connectors.tavily import CONNECTOR_ID
from product_factory.orchestration.coordinator import _RESEARCH_AGENT_MAX_ROUNDS
from product_factory.validation.pipeline import (
    request_expects_web_citations,
    validate_web_search_used,
)


def test_shipped_config_enables_tavily() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    assert config.connectors.is_enabled(CONNECTOR_ID)


def test_research_agent_round_budget_exceeds_prior_ten_round_cap() -> None:
    # run-3c3ef0d2d4ca exhausted 10 rounds mid-search; keep clear headroom.
    assert _RESEARCH_AGENT_MAX_ROUNDS >= 24


def test_request_expects_web_citations_from_text_and_metadata() -> None:
    assert request_expects_web_citations("Design storage and include Citations with URLs.")
    assert request_expects_web_citations("plain request", {"require_web_search": "true"})
    assert not request_expects_web_citations("Design a local architecture only.")


def test_validate_web_search_used_passes_when_invoked() -> None:
    result = validate_web_search_used(
        expected=True,
        connector_enabled=True,
        invocation_count=2,
    )
    assert result is not None
    assert result.status == "pass"


def test_validate_web_search_used_fails_when_never_invoked() -> None:
    result = validate_web_search_used(
        expected=True,
        connector_enabled=True,
        invocation_count=0,
    )
    assert result is not None
    assert result.status == "fail"
    assert result.validator_id == "web_search_used"


def test_validate_web_search_used_skips_when_not_expected_or_disabled() -> None:
    assert (
        validate_web_search_used(
            expected=False,
            connector_enabled=True,
            invocation_count=0,
        )
        is None
    )
    assert (
        validate_web_search_used(
            expected=True,
            connector_enabled=False,
            invocation_count=0,
        )
        is None
    )
