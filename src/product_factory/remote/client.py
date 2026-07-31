"""RemotePfClient — HTTPS transport for product-factory.host/v1 (PM2.B2)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from product_factory.delivery.models import DeliveryManifest, LandingReceipt
from product_factory.host.protocol import HOST_PROTOCOL, HostResponse
from product_factory.remote.sse import wait_for_terminal


class PfProtocolError(RuntimeError):
    """Remote endpoint spoke an unexpected protocol version."""

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.detail = detail


class PfRemoteError(RuntimeError):
    """Transport or configuration failure talking to a remote host."""


def resolve_remote_url(explicit: str | None = None) -> str:
    url = (explicit or os.environ.get("PRODUCT_FACTORY_REMOTE_URL") or "").strip().rstrip("/")
    if not url:
        raise PfRemoteError(
            "PRODUCT_FACTORY_REMOTE_URL (or --remote-url) is required for remote mode"
        )
    return url


def resolve_auth_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    return (
        os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN")
        or os.environ.get("PRODUCT_FACTORY_HOST_TOKEN")
        or None
    )


def assert_protocol(payload: dict[str, Any] | HostResponse) -> HostResponse:
    if isinstance(payload, HostResponse):
        res = payload
        protocol = res.protocol
    else:
        protocol = payload.get("protocol")
        if protocol != HOST_PROTOCOL:
            raise PfProtocolError(
                f"Unexpected host protocol {protocol!r}; expected {HOST_PROTOCOL}. "
                "Upgrade the product-factory server or this client.",
                detail=protocol,
            )
        try:
            res = HostResponse.model_validate(payload)
        except Exception as exc:
            raise PfProtocolError(
                f"Invalid host/v1 envelope: {exc}",
                detail=payload,
            ) from exc
    if res.protocol != HOST_PROTOCOL:
        raise PfProtocolError(
            f"Unexpected host protocol {res.protocol!r}; expected {HOST_PROTOCOL}. "
            "Upgrade the product-factory server or this client.",
            detail=res.protocol,
        )
    return res


class RemotePfClient:
    """Transport-neutral HTTP client for remote Product Factory hosts."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = resolve_remote_url(base_url)
        self.token = resolve_auth_token(token)
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=timeout,
                transport=transport,
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> RemotePfClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _parse(self, response: httpx.Response) -> HostResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PfRemoteError(
                f"Non-JSON response from {response.request.url} ({response.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise PfRemoteError(f"Expected JSON object from {response.request.url}")
        # Protocol mismatch fails closed even on HTTP error bodies that carry an envelope.
        if "protocol" in payload:
            return assert_protocol(payload)
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        raise PfRemoteError(
            f"Remote host returned HTTP {response.status_code} without host/v1 envelope"
        )

    def meta(self) -> dict[str, Any]:
        response = self._client.get("/api/v1/meta", headers=self._headers())
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PfRemoteError("meta response must be a JSON object")
        protocol = payload.get("protocol")
        if protocol is not None and protocol != HOST_PROTOCOL:
            raise PfProtocolError(
                f"Unexpected host protocol {protocol!r}; expected {HOST_PROTOCOL}.",
                detail=protocol,
            )
        return payload

    def submit(
        self,
        *,
        request_text: str,
        workflow_type: str = "code_change",
        repository_id: str | None = None,
        workspace: dict[str, Any] | None = None,
        repository_path: str | Path | None = None,
        model_profile_set: str = "local-target",
        validation_commands: list[str] | None = None,
        artifact_overrides: dict[str, Any] | None = None,
        pack_input: dict[str, Any] | None = None,
        handoff_refs: list[dict[str, Any]] | None = None,
        budget_usd: float = 3.0,
        max_wall_clock_seconds: int | None = None,
        request_id: str | None = None,
        mock: bool = False,
        sync: bool = False,
        inline: bool = False,
    ) -> HostResponse:
        if repository_path is not None:
            raise PfRemoteError(
                "Remote mode rejects repository_path; use repository_id or omit for no-repo runs"
            )
        if workspace is not None and repository_id is not None:
            raise PfRemoteError("workspace cannot be combined with repository_id")
        body: dict[str, Any] = {
            "request_text": request_text,
            "workflow_type": workflow_type,
            "model_profile_set": model_profile_set,
            "validation_commands": validation_commands or [],
            "artifact_overrides": artifact_overrides or {},
            "pack_input": pack_input or {},
            "handoff_refs": handoff_refs or [],
            "budget_usd": budget_usd,
            "mock": mock,
            "sync": sync,
            "inline": inline,
        }
        if repository_id is not None:
            body["repository_id"] = repository_id
        if workspace is not None:
            body["workspace"] = dict(workspace)
        if max_wall_clock_seconds is not None:
            body["max_wall_clock_seconds"] = max_wall_clock_seconds
        if request_id is not None:
            body["request_id"] = request_id
        response = self._client.post("/api/v1/runs", json=body, headers=self._headers())
        return self._parse(response)

    def status(self, run_id: str) -> HostResponse:
        response = self._client.get(f"/api/v1/runs/{run_id}/status", headers=self._headers())
        return self._parse(response)

    def inspect(self, run_id: str) -> HostResponse:
        response = self._client.get(f"/api/v1/runs/{run_id}/inspect", headers=self._headers())
        return self._parse(response)

    def tail(self, run_id: str, *, after_seq: int = 0) -> HostResponse:
        response = self._client.get(
            f"/api/v1/runs/{run_id}/tail",
            params={"after_seq": after_seq},
            headers=self._headers(),
        )
        return self._parse(response)

    def approve(self, run_id: str, *, apply: bool = False) -> HostResponse:
        response = self._client.post(
            f"/api/v1/runs/{run_id}/approve",
            json={"apply": apply},
            headers=self._headers(),
        )
        return self._parse(response)

    def delivery(self, run_id: str) -> DeliveryManifest:
        response = self._client.get(
            f"/api/v1/runs/{run_id}/delivery",
            headers=self._headers(),
        )
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        if not response.is_success:
            raise PfRemoteError(
                f"Delivery manifest request failed ({response.status_code}): {response.text}"
            )
        try:
            return DeliveryManifest.model_validate(response.json())
        except Exception as exc:
            raise PfProtocolError("Invalid delivery manifest", detail=response.text) from exc

    def delivery_blob(self, run_id: str, sha256: str) -> bytes:
        response = self._client.get(
            f"/api/v1/runs/{run_id}/delivery/blobs/{sha256}",
            headers={**self._headers(), "Accept": "application/octet-stream"},
        )
        if not response.is_success:
            raise PfRemoteError(
                f"Delivery blob request failed ({response.status_code}): {response.text}"
            )
        return response.content

    def record_landing(self, run_id: str, receipt: LandingReceipt) -> dict[str, Any]:
        response = self._client.post(
            f"/api/v1/runs/{run_id}/delivery/receipts",
            json=receipt.model_dump(mode="json"),
            headers=self._headers(),
        )
        if not response.is_success:
            raise PfRemoteError(
                f"Landing receipt request failed ({response.status_code}): {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise PfProtocolError("Landing receipt response must be an object", detail=payload)
        return payload

    def reject(self, run_id: str) -> HostResponse:
        response = self._client.post(f"/api/v1/runs/{run_id}/reject", headers=self._headers())
        return self._parse(response)

    def cancel(self, run_id: str) -> HostResponse:
        response = self._client.post(f"/api/v1/runs/{run_id}/cancel", headers=self._headers())
        return self._parse(response)

    def wait(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        timeout: float = 600.0,
        poll_interval: float = 0.5,
        wanted: set[str] | None = None,
    ) -> HostResponse:
        """Wait until a terminal/review status via SSE with poll-status fallback."""
        return wait_for_terminal(
            self,
            run_id,
            after_seq=after_seq,
            timeout=timeout,
            poll_interval=poll_interval,
            wanted=wanted,
        )

    def iter_sse(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        live: bool = True,
    ) -> Iterator[dict[str, Any]]:
        from product_factory.remote.sse import iter_sse_events

        yield from iter_sse_events(
            self._client,
            run_id,
            after_seq=after_seq,
            live=live,
            headers=self._headers(),
        )
