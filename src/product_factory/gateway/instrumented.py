"""Gateway decorator that records model invocations and observability events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse
from product_factory.observability.contracts import EventSeverity
from product_factory.observability.ids import new_span_id
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.persistence.database import Database


class InstrumentedModelGateway(ModelGateway):
    def __init__(
        self,
        inner: ModelGateway,
        *,
        recorder: TelemetryRecorder | None = None,
        db: Database | None = None,
        ledger: BudgetLedger | None = None,
    ) -> None:
        self.inner = inner
        self.recorder = recorder
        self.db = db
        self.ledger = ledger

    def complete(self, request: ModelRequest) -> ModelResponse:
        # Single choke point for every model call (planning, review, architecture,
        # implementation/repair agent loops) — enforce run-level budgets here (P1.A).
        if self.ledger is not None:
            self.ledger.check_before_model()
        started = datetime.now(UTC).isoformat()
        span_id = new_span_id()
        parent_span_id = None
        if self.recorder is not None:
            _, parent_span_id = self.recorder.ensure_trace(request.run_id)
            self.recorder.emit(
                run_id=request.run_id,
                event_type="model.request.started",
                task_id=request.task_id,
                request_id=request.request_id,
                summary=f"Model request {request.model_profile}",
                payload={
                    "model_profile": request.model_profile,
                    "max_output_tokens": request.max_output_tokens,
                    "message_count": len(request.messages),
                },
                content=[m.model_dump(mode="json") for m in request.messages],
                content_logical_name=f"prompt-{request.request_id}",
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
        try:
            resp = self.inner.complete(request)
        except Exception as exc:
            if self.recorder is not None:
                self.recorder.emit(
                    run_id=request.run_id,
                    event_type="model.request.failed",
                    task_id=request.task_id,
                    request_id=request.request_id,
                    severity=EventSeverity.ERROR,
                    summary=str(exc),
                    payload={"error": str(exc), "model_profile": request.model_profile},
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                )
            if self.db is not None:
                self.db.record_invocation(
                    request_id=request.request_id,
                    run_id=request.run_id,
                    task_id=request.task_id,
                    model_profile=request.model_profile,
                    status="provider_error",
                    usage={},
                    response_hash=None,
                    started_at=started,
                    ended_at=datetime.now(UTC).isoformat(),
                )
            raise

        ended = datetime.now(UTC).isoformat()
        if self.db is not None:
            self.db.record_invocation(
                request_id=request.request_id,
                run_id=request.run_id,
                task_id=request.task_id,
                model_profile=request.model_profile,
                status=resp.status,
                usage=resp.usage.model_dump(mode="json"),
                response_hash=resp.response_hash,
                provider=resp.provider,
                resolved_model_id=resp.resolved_model_id,
                started_at=started,
                ended_at=ended,
                latency_ms=resp.latency_ms,
            )
        if self.recorder is not None:
            self.recorder.emit(
                run_id=request.run_id,
                event_type="model.request.completed",
                task_id=request.task_id,
                request_id=request.request_id,
                summary=f"Model {resp.status}",
                payload={
                    "model_profile": request.model_profile,
                    "provider": resp.provider,
                    "resolved_model_id": resp.resolved_model_id,
                    "status": resp.status,
                    "usage": resp.usage.model_dump(mode="json"),
                    "latency_ms": resp.latency_ms,
                    "response_hash": resp.response_hash,
                },
                content={
                    "text": resp.text,
                    "structured_data": resp.structured_data,
                    "tool_calls": [t.model_dump(mode="json") for t in resp.tool_calls],
                },
                content_logical_name=f"response-{request.request_id}",
                span_id=span_id,
                parent_span_id=parent_span_id,
            )
        if self.ledger is not None:
            self.ledger.record_usage(resp.usage)
        return resp

    def refresh_catalog(self) -> dict[str, Any]:
        return self.inner.refresh_catalog()

    def list_models(self) -> list[dict[str, Any]]:
        return self.inner.list_models()
