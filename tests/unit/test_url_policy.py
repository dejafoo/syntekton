"""URL fetch policy tests (PM1.B1).

Every case runs against a stub resolver: the policy must be provable without
touching DNS or the network.
"""

from __future__ import annotations

import socket
from collections.abc import Sequence
from typing import Any

import pytest

from product_factory.connectors.errors import (
    ConnectorEgressDenied,
    ConnectorPolicyDenied,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import EgressPolicy
from product_factory.connectors.url_policy import (
    UrlPolicyDenied,
    assert_fetchable,
    assert_redirect_allowed,
    assert_response_allowed,
    normalize_media_type,
)

EGRESS = EgressPolicy(mode="domains", allowed_domains=("docs.example.com", "cdn.example.org"))

PUBLIC_A = "93.184.216.34"
PUBLIC_B = "151.101.1.69"


def stub_resolver(mapping: dict[str, Sequence[str]]):
    """A `getaddrinfo`-shaped resolver over a fixed host -> addresses table."""

    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        try:
            addresses = mapping[host]
        except KeyError:
            raise socket.gaierror(f"no fixture address for {host}") from None
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses
        ]

    return resolve


PUBLIC_RESOLVER = stub_resolver({"docs.example.com": [PUBLIC_A], "cdn.example.org": [PUBLIC_B]})


def fetchable(url: str, **kwargs: Any):
    kwargs.setdefault("egress", EGRESS)
    kwargs.setdefault("resolver", PUBLIC_RESOLVER)
    return assert_fetchable(url, **kwargs)


def test_allowlisted_https_host_is_fetchable_and_pins_addresses() -> None:
    target = fetchable("https://docs.example.com/guide?v=2")
    assert (target.scheme, target.host, target.port) == ("https", "docs.example.com", 443)
    assert target.addresses == (PUBLIC_A,)
    assert target.hop == 0
    assert target.as_payload()["addresses"] == [PUBLIC_A]


def test_http_downgrade_is_denied() -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("http://docs.example.com/guide")
    assert excinfo.value.reason == "scheme_not_allowed"
    assert excinfo.value.denial_code == "url_policy:scheme_not_allowed"
    # Fail-closed handling that keys off the connector policy base still catches it.
    assert isinstance(excinfo.value, ConnectorPolicyDenied)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://docs.example.com/x", "//nowhere/x"])
def test_non_https_schemes_are_denied(url: str) -> None:
    with pytest.raises(UrlPolicyDenied, match="not fetchable"):
        fetchable(url)


def test_odd_port_is_denied() -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("https://docs.example.com:8443/guide")
    assert excinfo.value.reason == "port_not_allowed"
    assert excinfo.value.details["port"] == 8443


def test_explicit_default_port_is_allowed() -> None:
    assert fetchable("https://docs.example.com:443/guide").port == 443


def test_credentials_in_url_are_denied() -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("https://user:secret@docs.example.com/guide")
    assert excinfo.value.reason == "credentials_in_url"
    # The password must not travel in the denial details.
    assert "secret" not in str(excinfo.value.details)


def test_host_confusion_via_at_sign_is_denied() -> None:
    with pytest.raises(UrlPolicyDenied, match="embedded credentials"):
        fetchable("https://docs.example.com@evil.test/guide")


def test_off_allowlist_host_is_denied_before_resolution() -> None:
    with pytest.raises(ConnectorEgressDenied):
        fetchable("https://evil.test/guide", resolver=stub_resolver({}))


def test_host_resolving_to_private_address_is_denied() -> None:
    resolver = stub_resolver({"docs.example.com": ["10.0.0.5"]})
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("https://docs.example.com/guide", resolver=resolver)
    assert excinfo.value.reason == "private_address"


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("127.0.0.1", "loopback_address"),
        ("169.254.169.254", "link_local_address"),
        ("224.0.0.1", "multicast_address"),
        ("192.168.1.10", "private_address"),
        ("172.16.4.4", "private_address"),
        ("0.0.0.0", "unspecified_address"),
        ("240.0.0.1", "reserved_address"),
        ("::1", "loopback_address"),
        ("fd00::1", "private_address"),
        ("fe80::1", "link_local_address"),
        ("::ffff:127.0.0.1", "loopback_address"),
    ],
)
def test_non_public_addresses_are_denied(address: str, reason: str) -> None:
    resolver = stub_resolver({"docs.example.com": [address]})
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("https://docs.example.com/guide", resolver=resolver)
    assert excinfo.value.reason == reason


def test_dns_rebind_answer_denies_the_whole_name() -> None:
    """One public answer does not launder a private one in the same record set."""
    resolver = stub_resolver({"docs.example.com": [PUBLIC_A, "127.0.0.1"]})
    with pytest.raises(UrlPolicyDenied) as excinfo:
        fetchable("https://docs.example.com/guide", resolver=resolver)
    assert excinfo.value.reason == "loopback_address"
    assert excinfo.value.details["addresses"] == [PUBLIC_A, "127.0.0.1"]


def test_private_ip_literal_is_denied_without_resolution() -> None:
    egress = EgressPolicy(mode="domains", allowed_domains=("127.0.0.1", "docs.example.com"))
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_fetchable(
            "https://127.0.0.1/guide",
            egress=egress,
            resolver=stub_resolver({}),
        )
    assert excinfo.value.reason == "loopback_address"


def test_unresolvable_host_is_unavailable_not_a_policy_denial() -> None:
    with pytest.raises(ConnectorUnavailable):
        fetchable("https://docs.example.com/guide", resolver=stub_resolver({}))
    with pytest.raises(ConnectorUnavailable):
        fetchable(
            "https://docs.example.com/guide", resolver=stub_resolver({"docs.example.com": []})
        )


def test_empty_url_is_denied() -> None:
    with pytest.raises(UrlPolicyDenied, match="empty"):
        fetchable("   ")


def test_redirect_to_allowlisted_host_is_allowed_and_counts_the_hop() -> None:
    first = fetchable("https://docs.example.com/guide")
    second = assert_redirect_allowed(
        first,
        "https://cdn.example.org/guide.pdf",
        egress=EGRESS,
        resolver=PUBLIC_RESOLVER,
    )
    assert (second.host, second.hop, second.addresses) == ("cdn.example.org", 1, (PUBLIC_B,))


def test_relative_redirect_is_joined_against_the_previous_hop() -> None:
    first = fetchable("https://docs.example.com/a/b/guide")
    second = assert_redirect_allowed(
        first,
        "/other?page=2",
        egress=EGRESS,
        resolver=PUBLIC_RESOLVER,
    )
    assert second.url == "https://docs.example.com/other?page=2"


def test_redirect_off_allowlist_is_denied() -> None:
    first = fetchable("https://docs.example.com/guide")
    with pytest.raises(ConnectorEgressDenied):
        assert_redirect_allowed(
            first,
            "https://evil.test/payload",
            egress=EGRESS,
            resolver=stub_resolver({}),
        )


def test_redirect_to_private_address_is_denied() -> None:
    """The allowlisted first hop does not vouch for where it points."""
    first = fetchable("https://docs.example.com/guide")
    resolver = stub_resolver({"cdn.example.org": ["169.254.169.254"]})
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_redirect_allowed(
            first,
            "https://cdn.example.org/metadata",
            egress=EGRESS,
            resolver=resolver,
        )
    assert excinfo.value.reason == "link_local_address"


def test_redirect_downgrade_to_http_is_denied() -> None:
    first = fetchable("https://docs.example.com/guide")
    with pytest.raises(UrlPolicyDenied, match="not fetchable"):
        assert_redirect_allowed(
            first,
            "http://docs.example.com/guide",
            egress=EGRESS,
            resolver=PUBLIC_RESOLVER,
        )


def test_redirect_chain_is_bounded() -> None:
    target = fetchable("https://docs.example.com/guide")
    for _ in range(3):
        target = assert_redirect_allowed(
            target,
            "https://docs.example.com/next",
            egress=EGRESS,
            resolver=PUBLIC_RESOLVER,
        )
    assert target.hop == 3
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_redirect_allowed(
            target,
            "https://docs.example.com/next",
            egress=EGRESS,
            resolver=PUBLIC_RESOLVER,
        )
    assert excinfo.value.reason == "too_many_redirects"


@pytest.mark.parametrize(
    "content_type",
    [
        "text/html; charset=utf-8",
        "TEXT/HTML",
        "text/plain",
        "text/markdown",
        "application/json",
        "application/x-yaml",
        "application/pdf",
    ],
)
def test_allowed_media_types_pass(content_type: str) -> None:
    assert assert_response_allowed(content_type, 1_000) == normalize_media_type(content_type)


@pytest.mark.parametrize(
    "content_type",
    ["application/octet-stream", "image/png", "text/event-stream", "application/zip"],
)
def test_disallowed_media_types_are_denied(content_type: str) -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_response_allowed(content_type, 1_000)
    assert excinfo.value.reason == "content_type_not_allowed"


def test_missing_content_type_is_denied_rather_than_sniffed() -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_response_allowed("", 10)
    assert excinfo.value.reason == "missing_content_type"


def test_oversize_response_is_denied() -> None:
    with pytest.raises(UrlPolicyDenied) as excinfo:
        assert_response_allowed("text/html", 5_000, max_bytes=4_096)
    assert excinfo.value.reason == "response_too_large"
    assert excinfo.value.details["max_bytes"] == 4_096
    assert assert_response_allowed("text/html", 4_096, max_bytes=4_096) == "text/html"
