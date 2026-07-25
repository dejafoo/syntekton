"""Connector policy: what gets denied, and how failures are typed."""

from __future__ import annotations

import subprocess

import pytest

from product_factory.connectors.broker import (
    EVENT_DENIED,
    EVENT_FAILED,
    EVENT_INVOKED,
    ConnectorBroker,
)
from product_factory.connectors.errors import (
    ConnectorEgressDenied,
    ConnectorPolicyDenied,
    ConnectorTimeout,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import (
    ConnectorManifest,
    ConnectorToolSpec,
    EgressPolicy,
    domain_matches,
    host_of,
)
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorInvocation, ConnectorRegistry
from product_factory.connectors.result import ConnectorResult, Provenance, bound_payload
from product_factory.domain.errors import ProviderError, RuntimeFailureError, UnsafeOperationError

from .conftest import (
    ECHO_ID,
    WEB_ID,
    WRITE_ID,
    AuditSink,
    echo_handler,
    echo_manifest,
    enabled_config,
    registry_with,
    web_handler,
    web_manifest,
    writer_manifest,
)


def _invoke(broker: ConnectorBroker, tool_name: str, **arguments: object) -> dict:
    return broker.invoke(
        tool_name=tool_name,
        arguments=dict(arguments),
        task_id="t1",
        tool_call_id="tc-1",
        run_id="run-1",
    )


def test_enabled_read_only_connector_returns_bounded_untrusted_envelope(
    echo_broker: ConnectorBroker, audit: AuditSink
) -> None:
    result = _invoke(echo_broker, "fake_echo_tool", text="hello")

    assert result["result"] == {"echo": "hello", "mock": False}
    assert result["trust_label"] == "untrusted"
    assert result["connector_id"] == ECHO_ID
    assert result["truncated"] is False
    assert result["result_sha256"]
    assert result["provenance"][0]["source"] == "fake://echo"

    assert audit.types() == [EVENT_INVOKED]
    event = audit.of_type(EVENT_INVOKED)[0]
    assert event["policy_decision_id"].startswith("cd-")
    assert event["arguments_hash"]
    assert event["result_sha256"] == result["result_sha256"]
    assert event["run_id"] == "run-1"


def test_unregistered_tool_is_denied_and_audited(audit: AuditSink) -> None:
    broker = ConnectorBroker(ConnectorRegistry(), config=enabled_config(ECHO_ID), audit=audit)

    with pytest.raises(ConnectorPolicyDenied) as excinfo:
        _invoke(broker, "does_not_exist")

    assert "No connector provides tool" in str(excinfo.value)
    assert audit.types() == [EVENT_DENIED]
    assert audit.of_type(EVENT_DENIED)[0]["denial_code"] == "policy_denied"


def test_registered_but_disabled_connector_is_denied(audit: AuditSink) -> None:
    """Shipping a connector must not enable it. Default config denies."""
    broker = ConnectorBroker(
        registry_with((echo_manifest(), echo_handler)),
        config=ConnectorsConfig(),
        audit=audit,
    )

    with pytest.raises(ConnectorPolicyDenied) as excinfo:
        _invoke(broker, "fake_echo_tool", text="hi")

    assert "not enabled" in str(excinfo.value)
    assert audit.types() == [EVENT_DENIED]


def test_write_capable_connector_is_denied_until_operator_opts_in(audit: AuditSink) -> None:
    registry = registry_with((writer_manifest(), echo_handler))
    denied = ConnectorBroker(registry, config=enabled_config(WRITE_ID), audit=audit)

    with pytest.raises(ConnectorPolicyDenied) as excinfo:
        _invoke(denied, "fake_write_tool")
    assert "write-capable connectors are disabled" in str(excinfo.value)

    allowed = ConnectorBroker(
        registry,
        config=enabled_config(WRITE_ID, allow_write_connectors=True),
        audit=audit,
    )
    assert _invoke(allowed, "fake_write_tool")["trust_label"] == "untrusted"


def test_each_tool_resolves_only_to_its_owning_connector() -> None:
    """Enabling one connector must not make another connector's tools reachable."""
    registry = registry_with((echo_manifest(), echo_handler), (web_manifest(), web_handler))
    assert registry.for_tool("fake_search").manifest.connector_id == WEB_ID
    assert registry.for_tool("fake_echo_tool").manifest.connector_id == ECHO_ID

    only_echo = ConnectorBroker(registry, config=enabled_config(ECHO_ID))
    assert only_echo.enabled_tool_names() == {"fake_echo_tool"}
    with pytest.raises(ConnectorPolicyDenied, match="not enabled"):
        _invoke(only_echo, "fake_search", query="x")


def test_egress_denied_for_host_outside_allowlist(audit: AuditSink) -> None:
    broker = ConnectorBroker(
        registry_with((web_manifest(), web_handler)),
        config=enabled_config(WEB_ID),
        audit=audit,
        environ={"FAKE_WEB_API_KEY": "secret-key"},
    )

    with pytest.raises(ConnectorEgressDenied) as excinfo:
        _invoke(broker, "fake_search", query="x", url="https://evil.example.net/steal")

    assert "not in the egress allowlist" in str(excinfo.value)
    assert audit.of_type(EVENT_DENIED)[0]["denial_code"] == "egress_denied"
    # Denials are policy events, not provider failures.
    assert EVENT_FAILED not in audit.types()


def test_egress_allowed_for_declared_host(audit: AuditSink) -> None:
    broker = ConnectorBroker(
        registry_with((web_manifest(), web_handler)),
        config=enabled_config(WEB_ID),
        audit=audit,
        environ={"FAKE_WEB_API_KEY": "secret-key"},
    )

    result = _invoke(broker, "fake_search", query="x", url="https://api.example.com/v1/search")

    assert result["result"]["host"] == "api.example.com"
    assert audit.types() == [EVENT_INVOKED]


def test_config_can_narrow_egress_but_not_widen_it() -> None:
    manifest = web_manifest(
        egress=EgressPolicy(mode="domains", allowed_domains=("api.example.com", "docs.example.com"))
    )
    config = ConnectorsConfig(
        connectors={WEB_ID: ConnectorSettings(enabled=True, allowed_domains=("docs.example.com",))}
    )

    narrowed = config.effective_egress(manifest)
    assert narrowed.allowed_domains == ("docs.example.com",)

    broker = ConnectorBroker(
        registry_with((manifest, web_handler)),
        config=config,
        environ={"FAKE_WEB_API_KEY": "k"},
    )
    with pytest.raises(ConnectorEgressDenied):
        _invoke(broker, "fake_search", url="https://api.example.com/v1")

    # A domain the manifest never declared cannot be added by config.
    widening = ConnectorsConfig(
        connectors={WEB_ID: ConnectorSettings(enabled=True, allowed_domains=("evil.example.net",))}
    )
    with pytest.raises(ConnectorPolicyDenied):
        widening.effective_egress(manifest)


def test_config_cannot_grant_egress_to_a_network_free_connector() -> None:
    config = ConnectorsConfig(
        connectors={ECHO_ID: ConnectorSettings(enabled=True, allowed_domains=("api.example.com",))}
    )
    with pytest.raises(ConnectorPolicyDenied) as excinfo:
        config.effective_egress(echo_manifest())
    assert "declares no egress" in str(excinfo.value)


def test_network_free_connector_denies_any_egress_attempt() -> None:
    def reaching_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        invocation.assert_egress_allowed("https://api.example.com/x")
        return ConnectorResult(payload={})

    broker = ConnectorBroker(
        registry_with((echo_manifest(), reaching_handler)),
        config=enabled_config(ECHO_ID),
    )
    with pytest.raises(ConnectorEgressDenied) as excinfo:
        _invoke(broker, "fake_echo_tool", text="x")
    assert "declares no network egress" in str(excinfo.value)


def test_missing_credential_is_typed_unavailable_not_a_model_failure(audit: AuditSink) -> None:
    broker = ConnectorBroker(
        registry_with((web_manifest(), web_handler)),
        config=enabled_config(WEB_ID),
        audit=audit,
        environ={},
    )

    with pytest.raises(ConnectorUnavailable) as excinfo:
        _invoke(broker, "fake_search", query="x")

    error = excinfo.value
    assert error.details["auth_env_var"] == "FAKE_WEB_API_KEY"
    # The gateway falls back to another provider on ProviderError. A connector
    # outage must never look like one, or a run burns budget retrying models.
    assert not isinstance(error, ProviderError)
    assert not isinstance(error, RuntimeFailureError)


def test_credential_is_not_required_in_mock_mode() -> None:
    broker = ConnectorBroker(
        registry_with((web_manifest(), web_handler)),
        config=enabled_config(WEB_ID),
        environ={},
        mock=True,
    )
    result = _invoke(broker, "fake_search", query="x", url="https://api.example.com/s")
    assert result["result"]["host"] == "api.example.com"


def test_credential_value_never_appears_in_audit_or_result(audit: AuditSink) -> None:
    secret = "sk-super-secret-value"

    def leaky_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        assert invocation.secret == secret
        return ConnectorResult(payload={"used_auth": bool(invocation.secret)})

    broker = ConnectorBroker(
        registry_with((web_manifest(), leaky_handler)),
        config=enabled_config(WEB_ID),
        audit=audit,
        environ={"FAKE_WEB_API_KEY": secret},
    )
    result = _invoke(broker, "fake_search", query="x")

    assert secret not in str(result)
    assert secret not in str(audit.events)
    assert audit.of_type(EVENT_INVOKED)[0].get("auth_env_var") is None


def test_provider_outage_becomes_connector_unavailable(audit: AuditSink) -> None:
    def broken_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        raise ConnectionResetError("socket died")

    broker = ConnectorBroker(
        registry_with((echo_manifest(), broken_handler)),
        config=enabled_config(ECHO_ID),
        audit=audit,
    )

    with pytest.raises(ConnectorUnavailable) as excinfo:
        _invoke(broker, "fake_echo_tool", text="x")

    assert excinfo.value.details["error_type"] == "ConnectionResetError"
    assert not isinstance(excinfo.value, ProviderError)
    assert audit.types() == [EVENT_FAILED]
    assert audit.of_type(EVENT_FAILED)[0]["denial_code"] == "unavailable"


@pytest.mark.parametrize(
    "raised",
    [TimeoutError("slow"), subprocess.TimeoutExpired(cmd="npx", timeout=1)],
)
def test_handler_timeouts_become_connector_timeout(raised: BaseException) -> None:
    def slow_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        raise raised

    broker = ConnectorBroker(
        registry_with((echo_manifest(), slow_handler)),
        config=enabled_config(ECHO_ID),
    )

    with pytest.raises(ConnectorTimeout):
        _invoke(broker, "fake_echo_tool", text="x")


def test_handler_returning_the_wrong_type_is_an_outage_not_a_crash() -> None:
    broker = ConnectorBroker(
        registry_with((echo_manifest(), lambda invocation: "not a result")),
        config=enabled_config(ECHO_ID),
    )
    with pytest.raises(ConnectorUnavailable) as excinfo:
        _invoke(broker, "fake_echo_tool", text="x")
    assert "expected ConnectorResult or dict" in str(excinfo.value)


def test_plain_dict_handler_results_are_accepted() -> None:
    broker = ConnectorBroker(
        registry_with((echo_manifest(), lambda invocation: {"ok": True})),
        config=enabled_config(ECHO_ID),
    )
    result = _invoke(broker, "fake_echo_tool", text="x")
    assert result["result"] == {"ok": True}
    assert result["provenance"] == []


def test_oversized_results_are_truncated_and_flagged() -> None:
    def huge_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        return ConnectorResult(payload="x" * 5_000)

    broker = ConnectorBroker(
        registry_with((echo_manifest(max_result_bytes=1_000), huge_handler)),
        config=enabled_config(ECHO_ID),
    )
    result = _invoke(broker, "fake_echo_tool", text="x")

    assert result["truncated"] is True
    assert len(result["result"]) == 1_000


def test_operator_config_can_lower_the_result_ceiling() -> None:
    def huge_handler(invocation: ConnectorInvocation) -> ConnectorResult:
        return ConnectorResult(payload="y" * 5_000)

    config = ConnectorsConfig(
        connectors={ECHO_ID: ConnectorSettings(enabled=True, max_result_bytes=100)}
    )
    broker = ConnectorBroker(
        registry_with((echo_manifest(max_result_bytes=4_000), huge_handler)), config=config
    )
    result = _invoke(broker, "fake_echo_tool", text="x")
    assert len(result["result"]) == 100


def test_operator_config_can_lower_but_not_raise_a_timeout() -> None:
    manifest = web_manifest(timeout_seconds=30)
    config = ConnectorsConfig(
        connectors={WEB_ID: ConnectorSettings(enabled=True, max_timeout_seconds=5)}
    )
    assert config.effective_timeout(manifest, 30) == 5

    raising = ConnectorsConfig(
        connectors={WEB_ID: ConnectorSettings(enabled=True, max_timeout_seconds=90)}
    )
    assert raising.effective_timeout(manifest, 30) == 30


def test_approval_requirement_denies_until_granted() -> None:
    manifest = echo_manifest(requires_approval=True)
    registry = registry_with((manifest, echo_handler))

    unapproved = ConnectorBroker(registry, config=enabled_config(ECHO_ID))
    with pytest.raises(ConnectorPolicyDenied) as excinfo:
        _invoke(unapproved, "fake_echo_tool", text="x")
    assert "requires operator approval" in str(excinfo.value)

    approved = ConnectorBroker(
        registry, config=enabled_config(ECHO_ID), approvals=lambda m, t: True
    )
    assert _invoke(approved, "fake_echo_tool", text="x")["result"]["echo"] == "x"


def test_config_can_require_approval_a_manifest_did_not_ask_for() -> None:
    config = ConnectorsConfig(
        connectors={ECHO_ID: ConnectorSettings(enabled=True, require_approval=True)}
    )
    broker = ConnectorBroker(registry_with((echo_manifest(), echo_handler)), config=config)
    with pytest.raises(ConnectorPolicyDenied):
        _invoke(broker, "fake_echo_tool", text="x")


def test_policy_denials_are_also_unsafe_operation_errors() -> None:
    """Existing fail-closed handling keys off `UnsafeOperationError`."""
    assert issubclass(ConnectorPolicyDenied, UnsafeOperationError)
    assert issubclass(ConnectorEgressDenied, ConnectorPolicyDenied)
    assert ConnectorPolicyDenied("x").exit_code == 8
    assert ConnectorUnavailable("x").exit_code == 10


def test_retention_policy_controls_what_the_audit_trail_keeps() -> None:
    def handler(invocation: ConnectorInvocation) -> ConnectorResult:
        return ConnectorResult(payload={"body": "sensitive-payload"})

    for retention, expects_excerpt in (("none", False), ("hash_only", False), ("full", True)):
        audit = AuditSink()
        broker = ConnectorBroker(
            registry_with((echo_manifest(result_retention=retention), handler)),
            config=enabled_config(ECHO_ID),
            audit=audit,
        )
        result = _invoke(broker, "fake_echo_tool", text="x")
        event = audit.of_type(EVENT_INVOKED)[0]
        # The hash is always kept, so evidence stays verifiable even at retention "none".
        assert event["result_sha256"] == result["result_sha256"]
        if expects_excerpt:
            assert "sensitive-payload" in str(event["result_excerpt"])
        else:
            assert event["result_excerpt"] is None


def test_concurrency_limit_yields_a_typed_timeout() -> None:
    manifest = echo_manifest(max_concurrency=1, timeout_seconds=1)
    broker = ConnectorBroker(
        registry_with((manifest, echo_handler)), config=enabled_config(ECHO_ID)
    )
    # Hold the only slot, then prove a second call fails typed instead of hanging.
    broker._semaphore(manifest).acquire()
    with pytest.raises(ConnectorTimeout) as excinfo:
        broker.invoke(
            tool_name="fake_echo_tool",
            arguments={"text": "x"},
            task_id="t1",
            tool_call_id="tc-2",
            run_id="run-1",
        )
    assert "concurrency limit" in str(excinfo.value)


class TestManifestValidation:
    def test_a_manifest_must_declare_at_least_one_tool(self) -> None:
        with pytest.raises(ValueError, match="at least one tool"):
            ConnectorManifest(connector_id="x", version="1", provider="p", tool_class="c", tools=())

    def test_duplicate_tool_names_are_rejected(self) -> None:
        spec = ConnectorToolSpec(name="dup", description="d")
        with pytest.raises(ValueError, match="Duplicate connector tool names"):
            ConnectorManifest(
                connector_id="x",
                version="1",
                provider="p",
                tool_class="c",
                tools=(spec, spec.model_copy()),
            )

    def test_a_tool_cannot_exceed_its_connector_permissions(self) -> None:
        with pytest.raises(ValueError, match="beyond connector permissions"):
            ConnectorManifest(
                connector_id="x",
                version="1",
                provider="p",
                tool_class="c",
                permissions=frozenset({"read"}),
                tools=(
                    ConnectorToolSpec(name="t", description="d", permissions=frozenset({"write"})),
                ),
            )

    def test_egress_mode_and_domains_must_agree(self) -> None:
        with pytest.raises(ValueError, match="requires at least one allowed domain"):
            EgressPolicy(mode="domains")
        with pytest.raises(ValueError, match="cannot declare allowed domains"):
            EgressPolicy(mode="none", allowed_domains=("example.com",))

    def test_a_second_connector_cannot_claim_an_existing_tool_name(self) -> None:
        registry = registry_with((echo_manifest(), echo_handler))
        clashing = echo_manifest(connector_id="other_connector")
        with pytest.raises(ConnectorPolicyDenied, match="already provided by connector"):
            registry.register(clashing, echo_handler)

    def test_connector_tools_are_always_labelled_untrusted_in_the_registry(self) -> None:
        definition = echo_manifest().tool_definitions()[0]
        assert definition.result_may_be_untrusted is True
        assert definition.tool_class == "fake_read"
        assert definition.resource_scope == "connector"

    def test_manifest_payload_names_the_credential_variable_but_not_its_value(self) -> None:
        payload = web_manifest().as_payload()
        assert payload["auth_env_var"] == "FAKE_WEB_API_KEY"
        assert payload["permissions"] == ["read"]
        assert payload["egress_allowed_domains"] == ["api.example.com"]


class TestHostMatching:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("https://api.example.com/path?q=1", "api.example.com"),
            ("api.example.com:8443", "api.example.com"),
            ("http://user:pw@api.example.com/x", "api.example.com"),
            ("API.Example.COM", "api.example.com"),
            ("", ""),
        ],
    )
    def test_host_extraction(self, target: str, expected: str) -> None:
        assert host_of(target) == expected

    @pytest.mark.parametrize(
        ("host", "pattern", "expected"),
        [
            ("api.example.com", "example.com", True),
            ("api.example.com", "api.example.com", True),
            ("api.example.com", "*.example.com", True),
            ("example.com", "example.com", True),
            # The classic suffix-confusion attack must not match.
            ("example.com.attacker.net", "example.com", False),
            ("notexample.com", "example.com", False),
            ("api.example.com", "", False),
        ],
    )
    def test_domain_matching_compares_whole_labels(
        self, host: str, pattern: str, expected: bool
    ) -> None:
        assert domain_matches(host, pattern) is expected

    def test_deny_list_wins_over_allow_list(self) -> None:
        policy = EgressPolicy(
            mode="domains",
            allowed_domains=("example.com",),
            denied_domains=("internal.example.com",),
        )
        assert policy.assert_allowed("https://api.example.com") == "api.example.com"
        with pytest.raises(ConnectorEgressDenied, match="deny list"):
            policy.assert_allowed("https://internal.example.com/secrets")


class TestPayloadBounding:
    def test_strings_are_clipped_to_the_byte_ceiling(self) -> None:
        clipped, truncated = bound_payload("a" * 100, 10)
        assert (clipped, truncated) == ("a" * 10, True)

    def test_structured_payloads_degrade_to_truncated_json(self) -> None:
        clipped, truncated = bound_payload({"k": "v" * 500}, 50)
        assert truncated is True
        assert isinstance(clipped, str)
        assert len(clipped) <= 50

    def test_small_payloads_pass_through_untouched(self) -> None:
        payload = {"k": "v"}
        clipped, truncated = bound_payload(payload, 1_000)
        assert clipped == payload and truncated is False

    def test_a_zero_ceiling_disables_bounding(self) -> None:
        payload = {"k": "v" * 100}
        assert bound_payload(payload, 0) == (payload, False)


def test_provenance_defaults_to_a_retrieval_timestamp() -> None:
    payload = Provenance(source="https://example.com/doc", kind="url").as_payload()
    assert payload["retrieved_at"]
    assert payload["source"] == "https://example.com/doc"
