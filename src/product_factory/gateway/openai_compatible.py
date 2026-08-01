"""Generic OpenAI-compatible HTTP adapter for local or hosted runtimes."""

from __future__ import annotations

import os
from typing import Any

import httpx

from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse
from product_factory.gateway.openrouter import OpenRouterGateway


class OpenAICompatibleGateway(OpenRouterGateway):
    """OpenAI `/v1/chat/completions` and `/v1/models` implementation.

    The adapter deliberately omits OpenRouter's provider-routing extension so
    the same profile can target vLLM, llama.cpp, or another compatible runtime.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str | None = None,
        profile_models: dict[str, dict[str, Any]] | None = None,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key_env = api_key_env
        super().__init__(
            api_key=os.environ.get(api_key_env, "") if api_key_env else "",
            base_url=base_url,
            profile_models=profile_models,
            max_retries=max_retries,
            client=client,
        )

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=120.0)

    def _build_payload(
        self,
        request: ModelRequest,
        model_id: str,
        *,
        structured_mode: str = "json_schema",
    ) -> dict[str, Any]:
        payload = super()._build_payload(
            request,
            model_id,
            structured_mode=structured_mode,
        )
        payload.pop("provider", None)
        payload.pop("user", None)
        return payload

    def _normalize(
        self,
        request: ModelRequest,
        data: dict[str, Any],
        model_id: str,
        latency_ms: int,
        retries: int,
    ) -> ModelResponse:
        response = super()._normalize(request, data, model_id, latency_ms, retries)
        return response.model_copy(
            update={
                "provider": "openai_compatible",
                "raw_response_ref": f"openai-compatible:{request.request_id}",
            }
        )
