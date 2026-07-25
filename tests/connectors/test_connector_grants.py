"""Which connector tools a task may be granted.

The coordinator builds a task's grant from tool classes, and its default branch
is permissive: with no declared classes, low-risk tools are granted. External
providers must never ride along on that default, so connector grants are
resolved by `grantable_tool_names` and require both a pack request and an
operator opt-in.
"""

from __future__ import annotations

from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.defaults import default_connector_registry
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings

from .conftest import (
    ECHO_ID,
    WEB_ID,
    WRITE_ID,
    echo_handler,
    echo_manifest,
    enabled_config,
    registry_with,
    web_handler,
    web_manifest,
    writer_manifest,
)


def _broker(config: ConnectorsConfig) -> ConnectorBroker:
    return ConnectorBroker(
        registry_with((echo_manifest(), echo_handler), (web_manifest(), web_handler)),
        config=config,
        environ={"FAKE_WEB_API_KEY": "k"},
    )


def test_no_connector_ships_enabled() -> None:
    """A fresh install has no connectors registered, so nothing can be granted."""
    registry = default_connector_registry(ConnectorsConfig())
    assert registry.tool_names() == frozenset()
    assert ConnectorBroker(registry).grantable_tool_names({"web_read"}) == frozenset()


def test_requesting_the_tool_class_grants_an_enabled_connector() -> None:
    broker = _broker(enabled_config(WEB_ID))
    assert broker.grantable_tool_names({"web_read"}) == {"fake_search"}


def test_requesting_the_tool_class_grants_nothing_when_disabled() -> None:
    broker = _broker(ConnectorsConfig())
    assert broker.grantable_tool_names({"web_read"}) == frozenset()


def test_an_enabled_connector_is_not_granted_without_the_tool_class() -> None:
    broker = _broker(enabled_config(WEB_ID))
    assert broker.grantable_tool_names({"repository_read", "git_read"}) == frozenset()


def test_tasks_with_no_declared_tool_classes_get_no_connectors() -> None:
    """The permissive default grant path must not reach an external provider."""
    broker = _broker(enabled_config(WEB_ID, ECHO_ID))
    assert broker.grantable_tool_names(set()) == frozenset()
    assert broker.grantable_tool_names({""}) == frozenset()


def test_enabling_one_connector_does_not_grant_another() -> None:
    broker = _broker(enabled_config(ECHO_ID))
    assert broker.grantable_tool_names({"web_read", "fake_read"}) == {"fake_echo_tool"}


def test_a_write_capable_connector_is_never_grantable_by_default() -> None:
    registry = registry_with((writer_manifest(), echo_handler))
    denied = ConnectorBroker(registry, config=enabled_config(WRITE_ID))
    assert denied.grantable_tool_names({"fake_write"}) == frozenset()

    permitted = ConnectorBroker(
        registry, config=enabled_config(WRITE_ID, allow_write_connectors=True)
    )
    assert permitted.grantable_tool_names({"fake_write"}) == {"fake_write_tool"}


def test_multiple_tool_classes_grant_the_union() -> None:
    broker = _broker(
        ConnectorsConfig(
            connectors={
                ECHO_ID: ConnectorSettings(enabled=True),
                WEB_ID: ConnectorSettings(enabled=True),
            }
        )
    )
    assert broker.grantable_tool_names({"fake_read", "web_read"}) == {
        "fake_echo_tool",
        "fake_search",
    }
