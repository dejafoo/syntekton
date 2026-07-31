"""Search-gated, SSRF-hardened public source retrieval."""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from product_factory.connectors.errors import ConnectorTimeout, ConnectorUnavailable
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec, EgressPolicy
from product_factory.connectors.registry import ConnectorInvocation
from product_factory.connectors.result import ConnectorResult, Provenance
from product_factory.connectors.source_ledger import SourceLedger
from product_factory.connectors.url_policy import (
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_MAX_RESPONSE_BYTES,
    FetchTarget,
    assert_fetchable,
    assert_redirect_allowed,
    assert_response_allowed,
)

CONNECTOR_ID = "source_fetch"
TOOL_FETCH_SOURCE = "fetch_source"
TOOL_CLASS_SOURCE_READ = "source_read"

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

FETCH_SOURCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "A URL admitted by this run's source ledger."}
    },
    "required": ["url"],
    "additionalProperties": False,
}


def source_fetch_manifest(*, allowed_domains: tuple[str, ...] = ("*",)) -> ConnectorManifest:
    return ConnectorManifest(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        provider="public_web",
        tool_class=TOOL_CLASS_SOURCE_READ,
        description="Retrieve a search-gated public source without returning its raw body inline",
        risk_class="R2",
        permissions=frozenset({"read"}),
        tools=(
            ConnectorToolSpec(
                name=TOOL_FETCH_SOURCE,
                description=(
                    "Fetch a URL previously returned by web_search or declared as an "
                    "operator seed. The body is persisted and only hashes are returned."
                ),
                input_schema=FETCH_SOURCE_SCHEMA,
                permissions=frozenset({"read"}),
                risk_class="R2",
                timeout_seconds=30,
            ),
        ),
        egress=EgressPolicy(mode="domains", allowed_domains=allowed_domains),
        timeout_seconds=30,
        max_concurrency=2,
        result_retention="hash_only",
        max_result_bytes=16_000,
    )


def _pinned_url(target: FetchTarget, address: str) -> str:
    parsed = urlsplit(target.url)
    host = f"[{address}]" if ":" in address else address
    netloc = host if target.port == 443 else f"{host}:{target.port}"
    return urlunsplit((target.scheme, netloc, parsed.path or "/", parsed.query, ""))


def _request(client: httpx.Client, target: FetchTarget, *, timeout: float) -> httpx.Response:
    """Connect to an address already approved by ``assert_fetchable``.

    The original hostname remains in Host and TLS SNI while the URL's network
    destination is the pinned IP. This avoids a second DNS lookup.
    """
    address = target.addresses[0]
    host_header = target.host if target.port == 443 else f"{target.host}:{target.port}"
    request = client.build_request(
        "GET",
        _pinned_url(target, address),
        headers={"Host": host_header, "Accept": "*/*"},
        timeout=timeout,
        extensions={"sni_hostname": target.host},
    )
    return client.send(request, stream=True, follow_redirects=False)


def fetch_source(invocation: ConnectorInvocation) -> ConnectorResult:
    url = str(invocation.arguments.get("url") or "").strip()
    ledger = invocation.options.get("source_ledger")
    if not isinstance(ledger, SourceLedger):
        raise ConnectorUnavailable(
            "fetch_source requires a run-scoped source ledger",
            connector_id=invocation.connector_id,
            tool_name=invocation.tool_name,
        )

    # Search/seed admission is deliberately checked before DNS or any I/O.
    ledger.assert_allowed(
        url,
        connector_id=invocation.connector_id,
        tool_name=invocation.tool_name,
    )
    resolver = invocation.options.get("resolver") or socket.getaddrinfo
    max_redirects = int(invocation.options.get("max_redirects", DEFAULT_MAX_REDIRECTS))
    max_bytes = int(invocation.options.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES))
    target = assert_fetchable(
        url,
        egress=invocation.egress,
        resolver=resolver,
        max_redirects=max_redirects,
        connector_id=invocation.connector_id,
        tool_name=invocation.tool_name,
    )

    client: httpx.Client | None = invocation.options.get("http_client")
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=float(invocation.timeout_seconds), follow_redirects=False)

    chain: list[FetchTarget] = [target]
    try:
        while True:
            try:
                response = _request(client, target, timeout=float(invocation.timeout_seconds))
            except httpx.TimeoutException as exc:
                raise ConnectorTimeout(
                    f"Source fetch timed out after {invocation.timeout_seconds}s",
                    connector_id=invocation.connector_id,
                    tool_name=invocation.tool_name,
                ) from exc
            except httpx.HTTPError as exc:
                raise ConnectorUnavailable(
                    f"Source fetch failed: {type(exc).__name__}",
                    connector_id=invocation.connector_id,
                    tool_name=invocation.tool_name,
                    details={"error_type": type(exc).__name__},
                ) from exc

            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "")
                    target = assert_redirect_allowed(
                        target,
                        location,
                        egress=invocation.egress,
                        resolver=resolver,
                        max_redirects=max_redirects,
                        connector_id=invocation.connector_id,
                        tool_name=invocation.tool_name,
                    )
                    chain.append(target)
                    continue
                if response.status_code >= 400:
                    raise ConnectorUnavailable(
                        f"Source returned HTTP {response.status_code}",
                        connector_id=invocation.connector_id,
                        tool_name=invocation.tool_name,
                        details={"status_code": response.status_code},
                    )

                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        break
                media_type = assert_response_allowed(
                    response.headers.get("content-type", ""),
                    len(body),
                    max_bytes=max_bytes,
                    connector_id=invocation.connector_id,
                    tool_name=invocation.tool_name,
                )
                raw_body = bytes(body)
                return ConnectorResult(
                    payload={
                        "media_type": media_type,
                        "bytes": len(raw_body),
                        "redirect_chain": [hop.as_payload() for hop in chain],
                    },
                    provenance=(Provenance(source=target.url, kind="url"),),
                    metadata={
                        "source_capture": {
                            "body": raw_body,
                            "url": target.url,
                            "media_type": media_type,
                            "redirect_chain": [hop.as_payload() for hop in chain],
                        }
                    },
                )
            finally:
                response.close()
    finally:
        if owns_client:
            client.close()


__all__ = [
    "CONNECTOR_ID",
    "FETCH_SOURCE_SCHEMA",
    "TOOL_CLASS_SOURCE_READ",
    "TOOL_FETCH_SOURCE",
    "fetch_source",
    "source_fetch_manifest",
]
