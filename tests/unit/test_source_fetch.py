from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorRegistry
from product_factory.connectors.source_fetch import (
    CONNECTOR_ID,
    fetch_source,
    source_fetch_manifest,
)
from product_factory.connectors.source_ledger import SourceLedger, SourceNotInLedger
from product_factory.connectors.url_policy import UrlPolicyDenied
from product_factory.domain.tools import CapabilityGrant
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


def _resolver(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


def _broker(
    tmp_path: Path,
    handler: httpx.MockTransport,
    *,
    domains: tuple[str, ...] = ("example.com",),
    max_bytes: int = 2_000_000,
) -> tuple[ToolBroker, ArtifactStore, SourceLedger]:
    connector_registry = ConnectorRegistry()
    connector_registry.register(
        source_fetch_manifest(allowed_domains=domains),
        fetch_source,
    )
    registry = default_tool_registry()
    for definition in connector_registry.tool_definitions():
        registry.register(definition)
    gate = SourceLedger.for_run(tmp_path / "run")
    store = ArtifactStore(tmp_path / "run" / "artifacts")
    broker = ToolBroker(
        registry=registry,
        artifact_store=store,
        connectors=ConnectorBroker(
            connector_registry,
            config=ConnectorsConfig(
                connectors={
                    CONNECTOR_ID: ConnectorSettings(
                        enabled=True,
                        options={
                            "http_client": httpx.Client(transport=handler),
                            "resolver": _resolver,
                            "max_response_bytes": max_bytes,
                        },
                    )
                }
            ),
            environ={},
        ),
        source_ledger=gate,
        run_id="run-1",
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g-1",
            run_id="run-1",
            task_id="T-001",
            agent_profile="researcher",
            tool_names={"fetch_source"},
            max_calls=5,
        )
    )
    return broker, store, gate


def test_fetch_pins_address_persists_capture_and_never_returns_body(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"evidence")

    broker, store, gate = _broker(tmp_path, httpx.MockTransport(respond))
    gate.record_search_results(["https://example.com/source"])

    envelope = broker.execute(
        task_id="T-001",
        tool_name="fetch_source",
        arguments={"url": "https://example.com/source"},
    )

    result = envelope["result"]
    assert set(result) == {
        "source_sha256",
        "record_sha256",
        "media_type",
        "bytes",
        "redirect_chain",
    }
    assert "evidence" not in str(envelope)
    assert store.get_bytes(result["source_sha256"]) == b"evidence"
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"
    assert result["redirect_chain"][0]["addresses"] == ["93.184.216.34"]


def test_fetch_requires_ledger_admission_before_request(tmp_path: Path) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x")

    broker, _, _ = _broker(tmp_path, httpx.MockTransport(respond))
    with pytest.raises(SourceNotInLedger):
        broker.execute(
            task_id="T-001",
            tool_name="fetch_source",
            arguments={"url": "https://example.com/source"},
        )
    assert calls == 0


def test_redirect_is_manually_revalidated_and_recorded(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.headers["host"] == "example.com":
            return httpx.Response(302, headers={"location": "https://docs.example.com/final"})
        return httpx.Response(200, headers={"content-type": "text/markdown"}, content=b"# Final")

    broker, _, gate = _broker(tmp_path, httpx.MockTransport(respond))
    gate.record_search_results(["https://example.com/source"])
    result = broker.execute(
        task_id="T-001",
        tool_name="fetch_source",
        arguments={"url": "https://example.com/source"},
    )["result"]
    assert [hop["url"] for hop in result["redirect_chain"]] == [
        "https://example.com/source",
        "https://docs.example.com/final",
    ]


def test_redirect_off_allowlist_is_denied(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "https://blocked.test/final"})
    )
    broker, _, gate = _broker(tmp_path, transport)
    gate.record_search_results(["https://example.com/source"])
    with pytest.raises(Exception, match="allowlist"):
        broker.execute(
            task_id="T-001",
            tool_name="fetch_source",
            arguments={"url": "https://example.com/source"},
        )


def test_oversize_body_is_denied_before_persistence(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"too large"
        )
    )
    broker, store, gate = _broker(tmp_path, transport, max_bytes=3)
    gate.record_search_results(["https://example.com/source"])
    with pytest.raises(UrlPolicyDenied) as excinfo:
        broker.execute(
            task_id="T-001",
            tool_name="fetch_source",
            arguments={"url": "https://example.com/source"},
        )
    assert excinfo.value.reason == "response_too_large"
    assert list(store.blobs.iterdir()) == []


def test_denied_host_never_reaches_transport(tmp_path: Path) -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x")

    broker, _, gate = _broker(tmp_path, httpx.MockTransport(respond), domains=("example.com",))
    gate.record_search_results(["https://blocked.test/source"])
    with pytest.raises(Exception, match="allowlist|not allowed|egress"):
        broker.execute(
            task_id="T-001",
            tool_name="fetch_source",
            arguments={"url": "https://blocked.test/source"},
        )
    assert calls == 0


def test_secret_body_is_blocked_by_ingress_before_persistence(tmp_path: Path) -> None:
    from product_factory.domain.errors import UnsafeOperationError

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"AKIAIOSFODNN7EXAMPLE embedded in docs",
        )
    )
    broker, store, gate = _broker(tmp_path, transport)
    gate.record_search_results(["https://example.com/source"])
    with pytest.raises(UnsafeOperationError):
        broker.execute(
            task_id="T-001",
            tool_name="fetch_source",
            arguments={"url": "https://example.com/source"},
        )
    assert list(store.blobs.iterdir()) == []
