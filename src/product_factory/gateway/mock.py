"""Mock OpenAI-compatible model gateway for tests and local portability."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse
from product_factory.gateway.errors import BudgetRejectedError

Responder = Callable[[ModelRequest], ModelResponse | dict[str, Any]]


class MockGateway(ModelGateway):
    """Deterministic mock adapter for contract tests and local profiles."""

    def __init__(
        self,
        *,
        responder: Responder | None = None,
        catalog: list[dict[str, Any]] | None = None,
        default_model: str = "mock/local-model",
    ) -> None:
        self._responder = responder
        self._catalog = catalog or [
            {
                "id": default_model,
                "context_length": 32_000,
                "pricing": {"prompt": "0", "completion": "0"},
            }
        ]
        self.calls: list[ModelRequest] = []
        self.default_model = default_model

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if request.max_cost_usd is not None and request.max_cost_usd <= 0:
            raise BudgetRejectedError("Request rejected: max_cost_usd <= 0")

        if self._responder is not None:
            result = self._responder(request)
            if isinstance(result, ModelResponse):
                return result
            return self._from_dict(request, result)

        structured: dict[str, Any] | None = None
        text = "mock response"
        if request.output_schema is not None:
            structured = {
                "objective": "mock objective",
                "assumptions": [],
                "tasks": [],
                "final_artifacts": [],
            }
            text = json.dumps(structured)

        body = text.encode("utf-8")
        return ModelResponse(
            request_id=request.request_id,
            provider="mock",
            provider_model_id=self.default_model,
            resolved_model_id=self.default_model,
            status="success",
            text=text,
            structured_data=structured,
            usage=UsageMetrics(
                input_tokens=10,
                output_tokens=20,
                estimated_cost_usd=Decimal("0"),
                latency_ms=1,
            ),
            latency_ms=1,
            finish_reason="stop",
            response_hash=hashlib.sha256(body).hexdigest(),
            raw_response_ref="mock:inline",
        )

    def refresh_catalog(self) -> dict[str, Any]:
        return {"models": self._catalog, "refreshed_at": time.time()}

    def list_models(self) -> list[dict[str, Any]]:
        return list(self._catalog)

    def _from_dict(self, request: ModelRequest, data: dict[str, Any]) -> ModelResponse:
        text = data.get("text")
        structured = data.get("structured_data")
        if structured is not None and text is None:
            text = json.dumps(structured)
        body = (text or "").encode("utf-8")
        return ModelResponse(
            request_id=request.request_id,
            provider=data.get("provider", "mock"),
            provider_model_id=data.get("provider_model_id", self.default_model),
            resolved_model_id=data.get("resolved_model_id", self.default_model),
            status=data.get("status", "success"),
            text=text,
            structured_data=structured,
            tool_calls=data.get("tool_calls", []),
            usage=UsageMetrics.model_validate(data.get("usage", {})),
            latency_ms=int(data.get("latency_ms", 1)),
            finish_reason=data.get("finish_reason", "stop"),
            response_hash=hashlib.sha256(body).hexdigest(),
            raw_response_ref=data.get("raw_response_ref", "mock:inline"),
        )
