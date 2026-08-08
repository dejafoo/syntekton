"""RemotePfClient — HTTPS transport for product-factory.host/v2 (SR4.B).

host/v1 remains an explicit compatibility path (`protocol="v1"`). Negotiation
may select v1 only when the server advertises no v2 support. Once v2 is locked,
execution failures never fall back to v1.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import httpx

from product_factory.delivery.models import DeliveryManifest, LandingReceipt
from product_factory.host.protocol import HOST_PROTOCOL, HostResponse
from product_factory.host.protocol_v2 import (
    HOST_PROTOCOL_V1,
    HOST_PROTOCOL_V2,
    HostResponseV2,
    SubmitRunV2Body,
)
from product_factory.remote.sse import wait_for_terminal

ProtocolChoice = Literal["auto", "v2", "v1"]
HostEnvelope = HostResponse | HostResponseV2


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
    """Strict host/v1 envelope decode (compatibility adapter)."""
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


def assert_protocol_v2(payload: dict[str, Any] | HostResponseV2) -> HostResponseV2:
    """Strict host/v2 envelope decode; unknown top-level fields are rejected."""
    if isinstance(payload, HostResponseV2):
        res = payload
    else:
        protocol = payload.get("protocol")
        if protocol != HOST_PROTOCOL_V2:
            raise PfProtocolError(
                f"Unexpected host protocol {protocol!r}; expected {HOST_PROTOCOL_V2}. "
                "Upgrade the product-factory server or this client.",
                detail=protocol,
            )
        unknown = set(payload) - set(HostResponseV2.model_fields)
        if unknown:
            raise PfProtocolError(
                f"host/v2 envelope has unknown fields: {sorted(unknown)}",
                detail=payload,
            )
        try:
            res = HostResponseV2.model_validate(payload)
        except Exception as exc:
            raise PfProtocolError(
                f"Invalid host/v2 envelope: {exc}",
                detail=payload,
            ) from exc
    if res.protocol != HOST_PROTOCOL_V2:
        raise PfProtocolError(
            f"Unexpected host protocol {res.protocol!r}; expected {HOST_PROTOCOL_V2}.",
            detail=res.protocol,
        )
    return res


class RemotePfClient:
    """HTTP client for remote Product Factory hosts.

    Defaults to host/v2. Pass ``protocol="v1"`` for the explicit compatibility
    adapter. ``protocol="auto"`` negotiates from metadata and prefers v2; it
    selects v1 only when the server does not advertise v2.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        protocol: ProtocolChoice = "v2",
    ) -> None:
        self.base_url = resolve_remote_url(base_url)
        self.token = resolve_auth_token(token)
        self._requested_protocol: ProtocolChoice = protocol
        self._locked_protocol: str | None = (
            HOST_PROTOCOL_V2 if protocol == "v2" else HOST_PROTOCOL_V1 if protocol == "v1" else None
        )
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

    @property
    def active_protocol(self) -> str | None:
        """Locked protocol id, or None before auto-negotiation completes."""
        return self._locked_protocol

    @property
    def uses_v2(self) -> bool:
        return self.ensure_protocol() == HOST_PROTOCOL_V2

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

    def ensure_protocol(self) -> str:
        """Return the locked protocol, negotiating once when ``protocol='auto'``."""
        if self._locked_protocol is not None:
            return self._locked_protocol
        meta = self._fetch_meta_for_negotiation()
        supported = meta.get("supported_protocols") or []
        if not isinstance(supported, list):
            supported = []
        if HOST_PROTOCOL_V2 in supported or meta.get("default_protocol") == HOST_PROTOCOL_V2:
            self._locked_protocol = HOST_PROTOCOL_V2
        elif HOST_PROTOCOL_V1 in supported or meta.get("protocol") == HOST_PROTOCOL_V1:
            # Classified compatibility: server has no v2 advertisement.
            self._locked_protocol = HOST_PROTOCOL_V1
        else:
            raise PfProtocolError(
                "Remote meta did not advertise a supported host protocol "
                f"(got supported_protocols={supported!r}, protocol={meta.get('protocol')!r})",
                detail=meta,
            )
        return self._locked_protocol

    def _fetch_meta_for_negotiation(self) -> dict[str, Any]:
        """Prefer /api/v2/meta; fall back to /api/v1/meta only for discovery."""
        for path in ("/api/v2/meta", "/api/v1/meta"):
            response = self._client.get(path, headers=self._headers())
            if response.status_code == 401:
                raise PfRemoteError("Unauthorized: missing or invalid bearer token")
            if response.status_code == 404:
                continue
            if not response.is_success:
                continue
            payload = response.json()
            if isinstance(payload, dict):
                return payload
        raise PfRemoteError("Remote host meta endpoints unavailable for protocol negotiation")

    def _parse_v1(self, response: httpx.Response) -> HostResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PfRemoteError(
                f"Non-JSON response from {response.request.url} ({response.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise PfRemoteError(f"Expected JSON object from {response.request.url}")
        if "protocol" in payload:
            return assert_protocol(payload)
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        raise PfRemoteError(
            f"Remote host returned HTTP {response.status_code} without host/v1 envelope"
        )

    def _parse_v2(self, response: httpx.Response) -> HostResponseV2:
        try:
            payload = response.json()
        except ValueError as exc:
            raise PfRemoteError(
                f"Non-JSON response from {response.request.url} ({response.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise PfRemoteError(f"Expected JSON object from {response.request.url}")
        if "protocol" in payload:
            return assert_protocol_v2(payload)
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        raise PfRemoteError(
            f"Remote host returned HTTP {response.status_code} without host/v2 envelope"
        )

    def _parse(self, response: httpx.Response) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            return self._parse_v2(response)
        return self._parse_v1(response)

    def meta(self) -> dict[str, Any]:
        protocol = self.ensure_protocol()
        path = "/api/v2/meta" if protocol == HOST_PROTOCOL_V2 else "/api/v1/meta"
        response = self._client.get(path, headers=self._headers())
        if response.status_code == 401:
            raise PfRemoteError("Unauthorized: missing or invalid bearer token")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise PfRemoteError("meta response must be a JSON object")
        expected = HOST_PROTOCOL_V2 if protocol == HOST_PROTOCOL_V2 else HOST_PROTOCOL
        advertised = payload.get("protocol")
        if advertised is not None and advertised != expected:
            raise PfProtocolError(
                f"Unexpected host protocol {advertised!r}; expected {expected}.",
                detail=advertised,
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
        model_profile_set: str | None = None,
        validation_commands: list[str] | None = None,
        artifact_overrides: dict[str, Any] | None = None,
        pack_input: dict[str, Any] | None = None,
        handoff_refs: list[dict[str, Any]] | None = None,
        handoffs: list[dict[str, Any]] | None = None,
        budget_usd: float = 3.0,
        max_wall_clock_seconds: int | None = None,
        request_id: str | None = None,
        metadata: dict[str, str] | None = None,
        mock: bool = False,
        sync: bool = False,
        inline: bool = False,
    ) -> HostEnvelope:
        if repository_path is not None:
            raise PfRemoteError(
                "Remote mode rejects repository_path; use repository_id or omit for no-repo runs"
            )
        if workspace is not None and repository_id is not None:
            raise PfRemoteError("workspace cannot be combined with repository_id")

        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            return self._submit_v2(
                request_text=request_text,
                workflow_type=workflow_type,
                repository_id=repository_id,
                validation_commands=validation_commands,
                artifact_overrides=artifact_overrides,
                pack_input=pack_input,
                handoffs=handoffs,
                handoff_refs=handoff_refs,
                budget_usd=budget_usd,
                max_wall_clock_seconds=max_wall_clock_seconds,
                request_id=request_id,
                metadata=metadata,
                model_profile_set=model_profile_set,
                mock=mock,
                sync=sync,
                inline=inline,
                workspace=workspace,
            )
        return self._submit_v1(
            request_text=request_text,
            workflow_type=workflow_type,
            repository_id=repository_id,
            workspace=workspace,
            model_profile_set=model_profile_set or "local-target",
            validation_commands=validation_commands,
            artifact_overrides=artifact_overrides,
            pack_input=pack_input,
            handoff_refs=handoff_refs,
            budget_usd=budget_usd,
            max_wall_clock_seconds=max_wall_clock_seconds,
            request_id=request_id,
            mock=mock,
            sync=sync,
            inline=inline,
        )

    def _submit_v2(
        self,
        *,
        request_text: str,
        workflow_type: str,
        repository_id: str | None,
        validation_commands: list[str] | None,
        artifact_overrides: dict[str, Any] | None,
        pack_input: dict[str, Any] | None,
        handoffs: list[dict[str, Any]] | None,
        handoff_refs: list[dict[str, Any]] | None,
        budget_usd: float,
        max_wall_clock_seconds: int | None,
        request_id: str | None,
        metadata: dict[str, str] | None,
        model_profile_set: str | None,
        mock: bool,
        sync: bool,
        inline: bool,
        workspace: dict[str, Any] | None,
    ) -> HostResponseV2:
        # Fail closed: never smuggle v1-only fields onto /api/v2, and never
        # silently retry through the v1 compatibility adapter.
        dead: list[str] = []
        if mock or sync or inline:
            dead.extend(
                [name for name, on in (("mock", mock), ("sync", sync), ("inline", inline)) if on]
            )
        if model_profile_set is not None:
            dead.append("model_profile_set")
        if handoff_refs:
            dead.append("handoff_refs")
        if workspace is not None:
            dead.append("workspace")
        if dead:
            raise PfRemoteError(
                "host/v2 rejects compatibility fields "
                f"{dead}; use protocol='v1' explicitly for the v1 adapter, "
                "or omit debug fields (mock/inline/sync are server-config only on v2). "
                "v2 handoffs use {handoff_id, expected_digest}."
            )
        body = SubmitRunV2Body.model_validate(
            {
                "request_text": request_text,
                "workflow_type": workflow_type,
                "repository_id": repository_id,
                "validation_commands": validation_commands or [],
                "artifact_overrides": artifact_overrides or {},
                "pack_input": pack_input or {},
                "handoffs": handoffs or [],
                "budget_usd": budget_usd,
                "max_wall_clock_seconds": max_wall_clock_seconds,
                "request_id": request_id,
                "metadata": metadata or {},
            }
        )
        response = self._client.post(
            "/api/v2/runs",
            json=body.model_dump(mode="json", exclude_none=True),
            headers=self._headers(),
        )
        return self._parse_v2(response)

    def _submit_v1(
        self,
        *,
        request_text: str,
        workflow_type: str,
        repository_id: str | None,
        workspace: dict[str, Any] | None,
        model_profile_set: str,
        validation_commands: list[str] | None,
        artifact_overrides: dict[str, Any] | None,
        pack_input: dict[str, Any] | None,
        handoff_refs: list[dict[str, Any]] | None,
        budget_usd: float,
        max_wall_clock_seconds: int | None,
        request_id: str | None,
        mock: bool,
        sync: bool,
        inline: bool,
    ) -> HostResponse:
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
        return self._parse_v1(response)

    def status(self, run_id: str) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            response = self._client.get(f"/api/v2/runs/{run_id}", headers=self._headers())
            return self._parse_v2(response)
        response = self._client.get(f"/api/v1/runs/{run_id}/status", headers=self._headers())
        return self._parse_v1(response)

    def inspect(self, run_id: str) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            response = self._client.get(f"/api/v2/runs/{run_id}/inspect", headers=self._headers())
            return self._parse_v2(response)
        response = self._client.get(f"/api/v1/runs/{run_id}/inspect", headers=self._headers())
        return self._parse_v1(response)

    def tail(self, run_id: str, *, after_seq: int = 0) -> HostResponse:
        """Event tail remains on /api/v1 until a v2 cursor batch ships."""
        response = self._client.get(
            f"/api/v1/runs/{run_id}/tail",
            params={"after_seq": after_seq},
            headers=self._headers(),
        )
        return self._parse_v1(response)

    def approve(self, run_id: str, *, apply: bool = False) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            response = self._client.post(
                f"/api/v2/runs/{run_id}/approve",
                json={"apply": apply},
                headers=self._headers(),
            )
            return self._parse_v2(response)
        response = self._client.post(
            f"/api/v1/runs/{run_id}/approve",
            json={"apply": apply},
            headers=self._headers(),
        )
        return self._parse_v1(response)

    def delivery(self, run_id: str) -> DeliveryManifest:
        # Delivery manifests are not yet versioned under /api/v2.
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

    def reject(self, run_id: str) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            response = self._client.post(f"/api/v2/runs/{run_id}/reject", headers=self._headers())
            return self._parse_v2(response)
        response = self._client.post(f"/api/v1/runs/{run_id}/reject", headers=self._headers())
        return self._parse_v1(response)

    def cancel(self, run_id: str) -> HostEnvelope:
        if self.ensure_protocol() == HOST_PROTOCOL_V2:
            response = self._client.post(f"/api/v2/runs/{run_id}/cancel", headers=self._headers())
            return self._parse_v2(response)
        response = self._client.post(f"/api/v1/runs/{run_id}/cancel", headers=self._headers())
        return self._parse_v1(response)

    def wait(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        timeout: float = 600.0,
        poll_interval: float = 0.5,
        wanted: set[str] | None = None,
    ) -> HostEnvelope:
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
