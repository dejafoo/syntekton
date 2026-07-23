"""OpenRouter HTTP adapter behind the provider-neutral gateway."""

from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal
from typing import Any

import httpx

from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalToolCall,
    ModelRequest,
    ModelResponse,
    ProviderPreferences,
)
from product_factory.gateway.errors import (
    BudgetRejectedError,
    NonRetryableProviderError,
    RetryableProviderError,
)
from product_factory.gateway.pricing import estimate_cost


class OpenRouterGateway(ModelGateway):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        profile_models: dict[str, dict[str, Any]] | None = None,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.profile_models = profile_models or {}
        self.max_retries = max_retries
        self._client = client
        self._catalog: list[dict[str, Any]] = []
        self._catalog_ts: float | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise NonRetryableProviderError("OPENROUTER_API_KEY is not set")
        return httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/product-factory/mvp",
                "X-Title": "product-factory",
            },
            timeout=120.0,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.max_cost_usd is not None and request.max_cost_usd <= 0:
            raise BudgetRejectedError("Request rejected by cost ceiling")

        profile = self.profile_models.get(request.model_profile, {})
        model_id = profile.get("model", request.model_profile)
        payload = self._build_payload(request, model_id)
        schema_fallback_used = False

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                client = self._get_client()
                owns_client = self._client is None
                try:
                    resp = client.post("/chat/completions", json=payload)
                    if (
                        not schema_fallback_used
                        and request.output_schema is not None
                        and resp.status_code == 400
                        and "json_schema" in resp.text.lower()
                    ):
                        schema_fallback_used = True
                        payload = self._build_payload(
                            request, model_id, structured_mode="json_object"
                        )
                        resp = client.post("/chat/completions", json=payload)
                finally:
                    if owns_client:
                        client.close()
                latency_ms = int((time.perf_counter() - started) * 1000)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RetryableProviderError(
                        f"Provider HTTP {resp.status_code}",
                        details={"status_code": resp.status_code},
                    )
                if resp.status_code == 401:
                    raise NonRetryableProviderError("Invalid API key")
                if resp.status_code >= 400:
                    raise NonRetryableProviderError(
                        f"Provider error HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                return self._normalize(request, data, model_id, latency_ms, attempt)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = RetryableProviderError(str(exc))
                if attempt >= self.max_retries:
                    break
                time.sleep(0.2 * (2**attempt))
            except RetryableProviderError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.2 * (2**attempt))

        assert last_error is not None
        raise last_error

    def refresh_catalog(self) -> dict[str, Any]:
        client = self._get_client()
        owns_client = self._client is None
        try:
            resp = client.get("/models")
            resp.raise_for_status()
            payload = resp.json()
            self._catalog = payload.get("data", payload.get("models", []))
            self._catalog_ts = time.time()
            return {"models": self._catalog, "refreshed_at": self._catalog_ts}
        finally:
            if owns_client:
                client.close()

    def list_models(self) -> list[dict[str, Any]]:
        if not self._catalog:
            self.refresh_catalog()
        return list(self._catalog)

    def _build_payload(
        self,
        request: ModelRequest,
        model_id: str,
        *,
        structured_mode: str = "json_schema",
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.name:
                item["name"] = message.name
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, default=str),
                        },
                    }
                    for call in message.tool_calls
                ]
            messages.append(item)
        if request.output_schema is not None and structured_mode == "json_object":
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Return ONLY valid JSON matching this schema:\n"
                        + json.dumps(request.output_schema)
                    ),
                }
            )
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.output_schema is not None:
            if structured_mode == "json_object":
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "structured_output",
                        "strict": True,
                        "schema": request.output_schema,
                    },
                }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        prefs = self._resolved_provider_preferences(request)
        provider: dict[str, Any] = {
            "allow_fallbacks": prefs.allow_fallbacks,
            "require_parameters": prefs.require_parameters,
            "data_collection": prefs.data_collection,
        }
        if prefs.sort:
            provider["sort"] = prefs.sort
        if prefs.order:
            provider["order"] = prefs.order
        if prefs.zdr is not None:
            provider["zdr"] = prefs.zdr
        payload["provider"] = provider
        payload["user"] = request.session_id
        return payload

    def _resolved_provider_preferences(self, request: ModelRequest) -> ProviderPreferences:
        """Merge profile `provider:` overrides from models.yaml onto request prefs.

        Anthropic frontier models often reject OpenRouter `require_parameters`
        when `seed` is set (404: no endpoints). Profiles can disable that flag.
        """
        profile = self.profile_models.get(request.model_profile, {})
        override = profile.get("provider") or {}
        if not isinstance(override, dict) or not override:
            return request.provider_preferences
        base = request.provider_preferences.model_dump()
        merged = {**base, **{k: v for k, v in override.items() if v is not None}}
        return ProviderPreferences.model_validate(merged)

    def _normalize(
        self,
        request: ModelRequest,
        data: dict[str, Any],
        model_id: str,
        latency_ms: int,
        retries: int,
    ) -> ModelResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content")
        finish = choice.get("finish_reason")
        tool_calls: list[CanonicalToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}
            tool_calls.append(
                CanonicalToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )

        structured: dict[str, Any] | None = None
        if request.output_schema is not None and text:
            try:
                structured = json.loads(text)
            except json.JSONDecodeError:
                return ModelResponse(
                    request_id=request.request_id,
                    provider="openrouter",
                    provider_model_id=model_id,
                    resolved_model_id=data.get("model", model_id),
                    status="invalid_output",
                    text=text,
                    usage=UsageMetrics(retries=retries, latency_ms=latency_ms),
                    latency_ms=latency_ms,
                    finish_reason=finish,
                    response_hash=hashlib.sha256((text or "").encode()).hexdigest(),
                )

        usage_raw = data.get("usage") or {}
        input_tokens = int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0)
        output_tokens = int(
            usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or 0
        )
        cached = int(usage_raw.get("cached_tokens") or 0)
        profile = self.profile_models.get(request.model_profile, {})
        pricing = profile.get("pricing") or {}
        est = estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price_per_million=pricing.get("input", "0"),
            output_price_per_million=pricing.get("output", "0"),
            cached_input_tokens=cached,
            cached_input_price_per_million=pricing.get("cached_input"),
        )
        reported = usage_raw.get("cost")
        status = "tool_calls" if tool_calls else "success"
        body = json.dumps(data, sort_keys=True).encode()
        return ModelResponse(
            request_id=request.request_id,
            provider="openrouter",
            provider_model_id=model_id,
            resolved_model_id=data.get("model", model_id),
            status=status,
            text=text,
            structured_data=structured,
            tool_calls=tool_calls,
            usage=UsageMetrics(
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                estimated_cost_usd=est,
                reported_cost_usd=Decimal(str(reported)) if reported is not None else None,
                latency_ms=latency_ms,
                retries=retries,
            ),
            latency_ms=latency_ms,
            finish_reason=finish,
            response_hash=hashlib.sha256(body).hexdigest(),
            raw_response_ref=f"openrouter:{request.request_id}",
        )
