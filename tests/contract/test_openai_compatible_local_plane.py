"""Contract tests against a controllable OpenAI-compatible fake server (RF5)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolDefinition,
    ModelRequest,
)
from product_factory.gateway.errors import (
    NonRetryableProviderError,
    RetryableProviderError,
)
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway


def _gateway(handler) -> OpenAICompatibleGateway:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:9/v1",
    )
    return OpenAICompatibleGateway(
        base_url="http://127.0.0.1:9/v1",
        profile_models={
            "local": {
                "model": "local/model",
                "pricing": {"input": "0", "output": "0"},
            }
        },
        client=client,
        max_retries=0,
    )


def _request(**overrides: Any) -> ModelRequest:
    payload = {
        "request_id": "c1",
        "run_id": "run",
        "task_id": "t",
        "session_id": "pf:contract:local",
        "model_profile": "local",
        "messages": [CanonicalMessage(role="user", content="hi")],
        "max_output_tokens": 32,
        "max_cost_usd": 0.5,
    }
    payload.update(overrides)
    return ModelRequest(**payload)


def test_non_streaming_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        assert "stream" not in body or body["stream"] is False
        assert "provider" not in body
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    response = _gateway(handler).complete(_request())
    assert response.provider == "openai_compatible"
    assert response.text == "hello"
    assert response.usage.input_tokens == 3


def test_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "echo"
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo",
                                        "arguments": '{"message":"ping"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    response = _gateway(handler).complete(
        _request(
            tools=[
                CanonicalToolDefinition(
                    name="echo",
                    description="echo",
                    parameters={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                )
            ]
        )
    )
    assert response.status == "tool_calls"
    assert response.tool_calls[0].name == "echo"
    assert response.tool_calls[0].arguments["message"] == "ping"


def test_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    response = _gateway(handler).complete(
        _request(
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            }
        )
    )
    assert response.structured_data == {"ok": True}


def test_timeout_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow local runtime")

    with pytest.raises(RetryableProviderError):
        _gateway(handler).complete(_request())


def test_malformed_response_is_non_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    with pytest.raises((NonRetryableProviderError, ValueError, json.JSONDecodeError)):
        _gateway(handler).complete(_request())


def test_usage_absence_still_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "local/model",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    response = _gateway(handler).complete(_request())
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.routing == {}


def test_models_catalogue_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={"data": [{"id": "local/model", "context_length": 8192}]},
        )

    probe = _gateway(handler).probe(model="local/model")
    assert probe.healthy
    assert probe.model_available
    assert probe.capabilities == frozenset()
