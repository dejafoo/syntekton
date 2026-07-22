"""Optional OpenTelemetry / OpenInference export bridge."""

from __future__ import annotations

import logging
import os
from typing import Any

from product_factory.observability.contracts import ObservabilityEvent

logger = logging.getLogger("product_factory.observability.otel")

# Map domain events → OpenInference span kinds / GenAI-ish names
_EVENT_KIND = {
    "run.started": "AGENT",
    "run.finished": "AGENT",
    "run.failed": "AGENT",
    "task.started": "CHAIN",
    "task.completed": "CHAIN",
    "task.failed": "CHAIN",
    "model.request.started": "LLM",
    "model.request.completed": "LLM",
    "model.request.failed": "LLM",
    "tool.call.started": "TOOL",
    "tool.call.completed": "TOOL",
    "tool.call.failed": "TOOL",
    "validation.completed": "GUARDRAIL",
    "prompt.package_created": "PROMPT",
}


class OtelBridge:
    """
    Best-effort OTLP exporter.

    Enabled when PRODUCT_FACTORY_OTLP_ENDPOINT is set and opentelemetry-sdk
    is installed. Never raises into the orchestration path.
    """

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = endpoint or os.environ.get("PRODUCT_FACTORY_OTLP_ENDPOINT")
        self._tracer: Any | None = None
        self._enabled = False
        if not self.endpoint:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": "product-factory"})
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint))
            )
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("product-factory")
            self._enabled = True
            logger.info("OTLP export enabled → %s", self.endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTLP setup failed (optional): %s", exc)
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def on_event(self, event: ObservabilityEvent) -> None:
        if not self._enabled or self._tracer is None:
            return
        kind = _EVENT_KIND.get(event.type, "CHAIN")
        # Emit a short span for completed/failed lifecycle edges; starts are attributes only.
        if event.type.endswith(".started"):
            return
        attributes: dict[str, str | int] = {
            "openinference.span.kind": kind,
            "gen_ai.operation.name": event.type,
            "product_factory.run_id": event.run_id,
            "product_factory.event_type": event.type,
            "product_factory.event_id": event.event_id,
        }
        if event.task_id:
            attributes["product_factory.task_id"] = event.task_id
        if event.request_id:
            attributes["gen_ai.request.id"] = event.request_id
        model = event.payload.get("resolved_model_id") or event.payload.get("model_profile")
        if model:
            attributes["gen_ai.request.model"] = str(model)
        usage = event.payload.get("usage") or {}
        if isinstance(usage, dict):
            if usage.get("input_tokens") is not None:
                attributes["gen_ai.usage.input_tokens"] = int(usage["input_tokens"])
            if usage.get("output_tokens") is not None:
                attributes["gen_ai.usage.output_tokens"] = int(usage["output_tokens"])
        with self._tracer.start_as_current_span(event.type) as span:
            for k, v in attributes.items():
                span.set_attribute(k, v)
            if event.type.endswith(".failed") or event.severity.value == "error":
                span.set_attribute("error.type", event.payload.get("error", event.summary))


def maybe_create_otel_bridge() -> OtelBridge | None:
    if not os.environ.get("PRODUCT_FACTORY_OTLP_ENDPOINT"):
        return None
    bridge = OtelBridge()
    return bridge if bridge.enabled else None
