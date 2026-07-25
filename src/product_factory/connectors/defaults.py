"""Built-in connector registration.

Registration is intentionally static process-startup code. Connectors appear
here or they do not exist; nothing in a run — plan, prompt, or provider response
— can add one. Registering is also not enabling: `connectors.yaml` still decides,
and a workflow pack still has to request the tool class.
"""

from __future__ import annotations

from product_factory.connectors.policy import ConnectorsConfig
from product_factory.connectors.registry import ConnectorRegistry


def default_connector_registry(config: ConnectorsConfig | None = None) -> ConnectorRegistry:
    """Every connector Product Factory ships with."""
    registry = ConnectorRegistry()
    _ = config
    return registry


__all__ = ["default_connector_registry"]
