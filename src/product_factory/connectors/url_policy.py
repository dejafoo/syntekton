"""URL fetch policy — what a retrieval connector may actually connect to (PM1.B1).

`EgressPolicy` answers one question: is this hostname allowlisted. That is not
enough for a tool that follows a URL chosen by a search result. A hostname on
the allowlist can resolve to `127.0.0.1`, a redirect can walk off the allowlist
one hop at a time, and a response can be a gigabyte of anything.

This module closes those gaps and stays free of any HTTP client, so it can be
exercised with a stub resolver and no network. Callers must connect to the
pinned addresses on the returned `FetchTarget` and re-check every redirect hop
through `assert_redirect_allowed`; resolving twice is the DNS-rebinding window.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from product_factory.connectors.errors import (
    ConnectorPolicyDenied,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import EgressPolicy

DEFAULT_ALLOWED_SCHEMES: tuple[str, ...] = ("https",)
DEFAULT_ALLOWED_PORTS: tuple[int, ...] = (443,)
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000

# Media types a discovery capture may be persisted as. Anything else — images,
# archives, executables, streams — has no place in a text evidence trail.
ALLOWED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "text/html",
        "text/plain",
        "text/markdown",
        "application/json",
        "application/yaml",
        "application/pdf",
    }
)

# Spellings that mean an allowed type. Everything else fails closed rather than
# being pattern-matched into the allowlist.
MEDIA_TYPE_ALIASES: dict[str, str] = {
    "application/x-yaml": "application/yaml",
    "text/yaml": "application/yaml",
    "text/x-yaml": "application/yaml",
    "text/x-markdown": "text/markdown",
    "application/markdown": "text/markdown",
}

_DEFAULT_PORTS: dict[str, int] = {"https": 443, "http": 80}

# `socket.getaddrinfo`-shaped: (family, type, proto, canonname, sockaddr).
Resolver = Callable[..., Sequence[Any]]


class UrlPolicyDenied(ConnectorPolicyDenied):
    """A URL or response violated the fetch policy.

    Sibling of `ConnectorEgressDenied` rather than a subclass: the host was
    allowlisted (or never got that far), and it is the scheme, port, credential,
    resolved address, redirect depth, media type, or size that is refused.
    """

    exit_code = 8

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        connector_id: str = "",
        tool_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            connector_id=connector_id,
            tool_name=tool_name,
            details={"reason": reason, **(details or {})},
        )
        self.reason = reason

    @property
    def denial_code(self) -> str:
        return f"url_policy:{self.reason}"


@dataclass(frozen=True)
class FetchTarget:
    """An approved destination, pinned to the addresses that were checked."""

    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]
    # 0 for the originally requested URL, incremented per redirect hop.
    hop: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "addresses": list(self.addresses),
            "hop": self.hop,
        }


def _deny(
    message: str,
    *,
    reason: str,
    connector_id: str,
    tool_name: str,
    details: dict[str, Any] | None = None,
) -> UrlPolicyDenied:
    return UrlPolicyDenied(
        message,
        reason=reason,
        connector_id=connector_id,
        tool_name=tool_name,
        details=details,
    )


def _ip_denial_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this address must not be fetched, or `None` when it is public.

    IPv6 transition formats carry an IPv4 address inside them, so `::ffff:127.0.0.1`
    is checked as `127.0.0.1` rather than as an opaque v6 address.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            return _ip_denial_reason(embedded)
        if ip.teredo is not None:
            return _ip_denial_reason(ip.teredo[1]) or "teredo_address"
    if ip.is_unspecified:
        return "unspecified_address"
    if ip.is_loopback:
        return "loopback_address"
    if ip.is_link_local:
        return "link_local_address"
    if ip.is_multicast:
        return "multicast_address"
    # Reserved before private: Python counts the reserved 240.0.0.0/4 block as
    # private too, and "reserved" is the more precise denial to audit.
    if ip.is_reserved:
        return "reserved_address"
    if ip.is_private:
        return "private_address"
    if not ip.is_global:
        return "non_global_address"
    return None


def _addresses_from(records: Iterable[Any]) -> tuple[str, ...]:
    """Pull the address strings out of `getaddrinfo` records.

    Plain strings are accepted too, so a test resolver can be a one-liner.
    """
    addresses: list[str] = []
    for record in records:
        if isinstance(record, str):
            candidate = record
        elif isinstance(record, tuple | list) and len(record) >= 5:
            sockaddr = record[4]
            if not isinstance(sockaddr, tuple | list) or not sockaddr:
                continue
            candidate = str(sockaddr[0])
        else:
            continue
        candidate = candidate.strip()
        if candidate and candidate not in addresses:
            addresses.append(candidate)
    return tuple(addresses)


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _resolve(
    host: str,
    port: int,
    *,
    resolver: Resolver,
    connector_id: str,
    tool_name: str,
) -> tuple[str, ...]:
    literal = _literal_ip(host)
    if literal is not None:
        return (str(literal),)
    try:
        records = resolver(host, port)
    except OSError as exc:
        raise ConnectorUnavailable(
            f"Could not resolve {host}",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"host": host, "error_type": type(exc).__name__},
        ) from exc
    addresses = _addresses_from(records or ())
    if not addresses:
        raise ConnectorUnavailable(
            f"No addresses for {host}",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"host": host},
        )
    return addresses


def assert_fetchable(
    url: str,
    *,
    egress: EgressPolicy,
    resolver: Resolver = socket.getaddrinfo,
    allowed_schemes: Sequence[str] = DEFAULT_ALLOWED_SCHEMES,
    allowed_ports: Sequence[int] = DEFAULT_ALLOWED_PORTS,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    hop: int = 0,
    connector_id: str = "",
    tool_name: str = "",
) -> FetchTarget:
    """Approve one destination, or raise.

    Checks run cheapest-and-most-decisive first so a denied URL is never
    resolved: scheme, embedded credentials, host, port, the connector's host
    allowlist, and only then DNS and the resolved addresses. Every address the
    name resolves to must be public — one private answer denies the whole name.
    """
    raw = str(url or "").strip()
    if not raw:
        raise _deny(
            "Fetch target is empty",
            reason="empty_url",
            connector_id=connector_id,
            tool_name=tool_name,
        )
    if hop > max_redirects:
        raise _deny(
            f"Redirect chain exceeded {max_redirects} hops",
            reason="too_many_redirects",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"url": raw, "hop": hop, "max_redirects": max_redirects},
        )

    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    schemes = tuple(s.lower() for s in allowed_schemes)
    if scheme not in schemes:
        raise _deny(
            f"Scheme {scheme or '(none)'!r} is not fetchable",
            reason="scheme_not_allowed",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"url": raw, "scheme": scheme, "allowed_schemes": list(schemes)},
        )
    if parsed.username or parsed.password or "@" in parsed.netloc:
        # Credentials in a URL are either an attempt to authenticate somewhere
        # this tool has no business authenticating, or a host-confusion trick.
        raise _deny(
            "Fetch target carries embedded credentials",
            reason="credentials_in_url",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"host": parsed.hostname or ""},
        )

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise _deny(
            "Fetch target has no host",
            reason="missing_host",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"url": raw},
        )
    try:
        port = parsed.port or _DEFAULT_PORTS.get(scheme, 0)
    except ValueError:
        raise _deny(
            "Fetch target has an unparseable port",
            reason="invalid_port",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"url": raw},
        ) from None
    if port not in tuple(allowed_ports):
        raise _deny(
            f"Port {port} is not fetchable",
            reason="port_not_allowed",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"host": host, "port": port, "allowed_ports": list(allowed_ports)},
        )

    # Host allowlisting stays with the connector's declared egress policy —
    # raises `ConnectorEgressDenied`, which is also a `ConnectorPolicyDenied`.
    egress.assert_allowed(raw, connector_id=connector_id, tool_name=tool_name)

    addresses = _resolve(
        host,
        port,
        resolver=resolver,
        connector_id=connector_id,
        tool_name=tool_name,
    )
    for address in addresses:
        ip = _literal_ip(address)
        if ip is None:
            raise _deny(
                f"Resolver returned an unparseable address for {host}",
                reason="unparseable_address",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"host": host, "address": address},
            )
        reason = _ip_denial_reason(ip)
        if reason is not None:
            raise _deny(
                f"{host} resolves to non-public address {address}",
                reason=reason,
                connector_id=connector_id,
                tool_name=tool_name,
                details={"host": host, "address": address, "addresses": list(addresses)},
            )

    return FetchTarget(
        url=raw,
        scheme=scheme,
        host=host,
        port=port,
        addresses=addresses,
        hop=hop,
    )


def assert_redirect_allowed(
    previous: FetchTarget,
    location: str,
    *,
    egress: EgressPolicy,
    resolver: Resolver = socket.getaddrinfo,
    allowed_schemes: Sequence[str] = DEFAULT_ALLOWED_SCHEMES,
    allowed_ports: Sequence[int] = DEFAULT_ALLOWED_PORTS,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    connector_id: str = "",
    tool_name: str = "",
) -> FetchTarget:
    """Re-run the full policy on a redirect hop.

    A relative `Location` is joined against the hop it came from. Nothing is
    inherited from `previous` except the hop counter: the allowlist, the port,
    and the resolved addresses are all decided again for the new URL.
    """
    target = urljoin(previous.url, str(location or "").strip())
    return assert_fetchable(
        target,
        egress=egress,
        resolver=resolver,
        allowed_schemes=allowed_schemes,
        allowed_ports=allowed_ports,
        max_redirects=max_redirects,
        hop=previous.hop + 1,
        connector_id=connector_id,
        tool_name=tool_name,
    )


def normalize_media_type(content_type: str) -> str:
    """Strip parameters and casing from a `Content-Type` header value."""
    base = str(content_type or "").split(";", 1)[0].strip().lower()
    return MEDIA_TYPE_ALIASES.get(base, base)


def assert_response_allowed(
    content_type: str,
    byte_len: int,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allowed_media_types: Iterable[str] = ALLOWED_MEDIA_TYPES,
    connector_id: str = "",
    tool_name: str = "",
) -> str:
    """Approve a response body by media type and size; return the media type.

    A missing or unrecognized `Content-Type` is a denial, not a guess: sniffing
    the body would mean trusting the bytes we are deciding whether to trust.
    """
    media_type = normalize_media_type(content_type)
    allowed = frozenset(allowed_media_types)
    if not media_type:
        raise _deny(
            "Response declares no content type",
            reason="missing_content_type",
            connector_id=connector_id,
            tool_name=tool_name,
        )
    if media_type not in allowed:
        raise _deny(
            f"Media type {media_type!r} is not retrievable",
            reason="content_type_not_allowed",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"media_type": media_type, "allowed": sorted(allowed)},
        )
    size = int(byte_len)
    if size < 0:
        raise _deny(
            "Response size is negative",
            reason="invalid_response_size",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"bytes": size},
        )
    if size > max_bytes:
        raise _deny(
            f"Response of {size} bytes exceeds the {max_bytes} byte cap",
            reason="response_too_large",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"bytes": size, "max_bytes": max_bytes},
        )
    return media_type


__all__ = [
    "ALLOWED_MEDIA_TYPES",
    "DEFAULT_ALLOWED_PORTS",
    "DEFAULT_ALLOWED_SCHEMES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "MEDIA_TYPE_ALIASES",
    "FetchTarget",
    "Resolver",
    "UrlPolicyDenied",
    "assert_fetchable",
    "assert_redirect_allowed",
    "assert_response_allowed",
    "normalize_media_type",
]
