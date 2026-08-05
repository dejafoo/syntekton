"""Remote ingress hardening: trusted proxy, rate limits, and audit (PM5.E / R5)."""

from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from product_factory.observability.redaction import redact_value

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class IngressConfig(BaseModel):
    """Operator-tunable remote ingress policy."""

    model_config = {"extra": "forbid"}

    # When empty, X-Forwarded-* / Forwarded headers are ignored (fail-closed).
    trusted_proxies: list[str] = Field(default_factory=list)
    trust_forwarded_headers: bool = False
    max_upload_bytes: int = Field(default=52_428_800, ge=1)  # 50 MiB
    allowed_upload_media_types: list[str] = Field(
        default_factory=lambda: [
            "application/x-git-bundle",
            "application/octet-stream",
        ]
    )
    max_upload_filename_bytes: int = Field(default=255, ge=1)
    auth_failure_limit: int = Field(default=20, ge=1)
    auth_failure_window_seconds: int = Field(default=60, ge=1)
    submit_rate_limit: int = Field(default=30, ge=1)
    submit_window_seconds: int = Field(default=60, ge=1)
    upload_rate_limit: int = Field(default=10, ge=1)
    upload_window_seconds: int = Field(default=60, ge=1)


def load_ingress_config(
    raw: dict[str, Any] | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> IngressConfig:
    """Merge policies.yaml ``ingress`` with optional env overrides."""
    env = environ if environ is not None else os.environ
    data = dict(raw or {})
    proxies = (env.get("PRODUCT_FACTORY_TRUSTED_PROXIES") or "").strip()
    if proxies:
        data["trusted_proxies"] = [p.strip() for p in proxies.split(",") if p.strip()]
    if (env.get("PRODUCT_FACTORY_TRUST_FORWARDED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        data["trust_forwarded_headers"] = True
    max_bytes = (env.get("PRODUCT_FACTORY_MAX_UPLOAD_BYTES") or "").strip()
    if max_bytes.isdigit():
        data["max_upload_bytes"] = int(max_bytes)
    return IngressConfig.model_validate(data)


def _parse_networks(entries: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in entries:
        text = entry.strip()
        if not text:
            continue
        try:
            if "/" in text:
                networks.append(ipaddress.ip_network(text, strict=False))
            else:
                addr = ipaddress.ip_address(text)
                networks.append(ipaddress.ip_network(f"{addr}/{addr.max_prefixlen}", strict=False))
        except ValueError:
            continue
    return networks


def _peer_host(request: Any) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return str(host or "")


def _peer_is_trusted(peer: str, networks: list[Any]) -> bool:
    if not peer:
        return False
    if peer in _LOOPBACK:
        return True
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def _first_forwarded_for(value: str | None) -> str | None:
    if not value:
        return None
    # Take the left-most (original client) hop; later hops are proxies.
    first = value.split(",", 1)[0].strip()
    if not first:
        return None
    # Strip optional port / quotes.
    if first.startswith('"') and first.endswith('"'):
        first = first[1:-1]
    if "]" in first and first.startswith("["):
        first = first[1 : first.index("]")]
    elif first.count(":") == 1 and first.rsplit(":", 1)[-1].isdigit():
        first = first.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(first)
    except ValueError:
        return None
    return first


def _forwarded_header_client(value: str | None) -> str | None:
    if not value:
        return None
    # RFC 7239: for=... may appear in a comma-separated list of forwarded-pairs.
    for part in value.split(","):
        for token in part.split(";"):
            token = token.strip()
            if token.lower().startswith("for="):
                raw = token.split("=", 1)[1].strip().strip('"')
                if raw.startswith("[") and "]" in raw:
                    raw = raw[1 : raw.index("]")]
                try:
                    ipaddress.ip_address(raw)
                    return raw
                except ValueError:
                    return None
    return None


def resolve_client_ip(request: Any, config: IngressConfig) -> str:
    """Return the client IP, ignoring forwarded headers unless explicitly trusted."""
    peer = _peer_host(request)
    headers = getattr(request, "headers", {}) or {}
    networks = _parse_networks(config.trusted_proxies)
    allow_forwarded = bool(config.trust_forwarded_headers and networks)
    if allow_forwarded and _peer_is_trusted(peer, networks):
        forwarded = _first_forwarded_for(headers.get("x-forwarded-for"))
        if forwarded:
            return forwarded
        forwarded = _forwarded_header_client(headers.get("forwarded"))
        if forwarded:
            return forwarded
    return peer or "unknown"


class RateLimiter:
    """In-process sliding-window limiter keyed by (bucket, client_ip)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, *, bucket: str, client_ip: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        key = (bucket, client_ip)
        with self._lock:
            q = self._hits[key]
            cutoff = now - window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


@dataclass
class IngressAuditor:
    """Append-only ingress audit log under the data root."""

    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, *, client_ip: str, **payload: Any) -> dict[str, Any]:
        record = {
            "occurred_at": datetime.now(UTC).isoformat(),
            "type": event_type,
            "client_ip": client_ip,
            "payload": redact_value(payload),
        }
        line = json.dumps(record, default=str) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return record


# Process-wide limiter shared by auth + control routes.
INGRESS_LIMITER = RateLimiter()
