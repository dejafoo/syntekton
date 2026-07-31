"""Connectors — third-party providers behind a declared policy contract.

A connector never grants capability. `ToolBroker` remains the sole execution
path (ADR-004); connectors are adapters it may dispatch to once a task's grant
already covers the tool name. Configuration alone does not make a provider
trusted: its manifest bounds what it may do, operator config can only narrow
that further, and every call is audited.
"""

from product_factory.connectors.broker import (
    EVENT_DENIED,
    EVENT_FAILED,
    EVENT_INVOKED,
    ConnectorBroker,
)
from product_factory.connectors.errors import (
    ConnectorEgressDenied,
    ConnectorError,
    ConnectorPolicyDenied,
    ConnectorTimeout,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import (
    ConnectorManifest,
    ConnectorToolSpec,
    EgressPolicy,
)
from product_factory.connectors.policy import (
    ConnectorsConfig,
    ConnectorSettings,
    load_connectors_config,
)
from product_factory.connectors.registry import (
    ConnectorHandler,
    ConnectorInvocation,
    ConnectorRegistry,
)
from product_factory.connectors.result import ConnectorResult, Provenance

__all__ = [
    "EVENT_DENIED",
    "EVENT_FAILED",
    "EVENT_INVOKED",
    "ConnectorBroker",
    "ConnectorEgressDenied",
    "ConnectorError",
    "ConnectorHandler",
    "ConnectorInvocation",
    "ConnectorManifest",
    "ConnectorPolicyDenied",
    "ConnectorRegistry",
    "ConnectorResult",
    "ConnectorSettings",
    "ConnectorTimeout",
    "ConnectorToolSpec",
    "ConnectorUnavailable",
    "ConnectorsConfig",
    "EgressPolicy",
    "Provenance",
    "load_connectors_config",
]
