"""Built-in connector registration.

Registration is intentionally static process-startup code. Connectors appear
here or they do not exist; nothing in a run — plan, prompt, or provider response
— can add one. Registering is also not enabling: `connectors.yaml` still decides,
and a workflow pack still has to request the tool class.
"""

from __future__ import annotations

from product_factory.connectors import tavily
from product_factory.connectors.policy import ConnectorsConfig
from product_factory.connectors.registry import ConnectorRegistry


def _result_domains(config: ConnectorsConfig) -> tuple[str, ...]:
    """Which hosts Tavily results may be quoted from.

    Defaults to any host Tavily returns. Listing hosts here restricts which
    sources a run can cite without also having to re-list the API endpoint,
    which must stay reachable for search to work at all.
    """
    raw = config.settings_for(tavily.CONNECTOR_ID).options.get("result_domains") or ()
    domains = tuple(str(domain).strip() for domain in raw if str(domain).strip())
    return domains or ("*",)


def default_connector_registry(config: ConnectorsConfig | None = None) -> ConnectorRegistry:
    """Every connector Product Factory ships with."""
    settings = config or ConnectorsConfig()
    registry = ConnectorRegistry()
    registry.register(
        tavily.tavily_manifest(allowed_result_domains=_result_domains(settings)),
        tavily.web_search,
    )
    return registry


__all__ = ["default_connector_registry"]
