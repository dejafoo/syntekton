"""OTel mapping unit tests (no live exporter required)."""

from __future__ import annotations

from product_factory.observability.contracts import ObservabilityEvent
from product_factory.observability.otel import _EVENT_KIND, OtelBridge


def test_event_kind_mapping() -> None:
    assert _EVENT_KIND["model.request.completed"] == "LLM"
    assert _EVENT_KIND["tool.call.started"] == "TOOL"
    assert _EVENT_KIND["validation.completed"] == "GUARDRAIL"


def test_bridge_disabled_without_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("PRODUCT_FACTORY_OTLP_ENDPOINT", raising=False)
    bridge = OtelBridge(endpoint=None)
    assert bridge.enabled is False
    bridge.on_event(
        ObservabilityEvent(
            event_id="e1",
            type="model.request.completed",
            run_id="r",
            summary="ok",
        )
    )
