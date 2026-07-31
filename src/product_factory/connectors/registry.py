"""Connector registry — manifests paired with handlers, resolved by tool name.

Registration is the only way a connector becomes reachable, and it happens in
process startup code, never from provider output. A result that claims a new
tool exists is just text; it cannot reach this registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from product_factory.connectors.errors import ConnectorPolicyDenied
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec, EgressPolicy
from product_factory.connectors.result import ConnectorResult
from product_factory.domain.tools import ToolDefinition


@dataclass(frozen=True)
class ConnectorInvocation:
    """Everything a handler is allowed to know about one call.

    Handlers must honour `timeout_seconds` on their own I/O and route every
    outbound host through `assert_egress_allowed`; the broker cannot see inside a
    handler's socket calls.
    """

    connector_id: str
    tool_name: str
    upstream_name: str
    arguments: dict[str, Any]
    task_id: str
    tool_call_id: str
    timeout_seconds: int
    manifest: ConnectorManifest
    tool: ConnectorToolSpec
    # Egress narrowed by operator config, not the manifest's raw declaration.
    egress: EgressPolicy = EgressPolicy()
    # Deterministic fixtures instead of real egress (--mock / PRODUCT_FACTORY_FORCE_MOCK).
    mock: bool = False
    # Provider credential, resolved by the broker from `manifest.auth_env_var`.
    # Never logged or echoed into results.
    secret: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    max_result_bytes: int = 64_000

    def assert_egress_allowed(self, target: str) -> str:
        return self.egress.assert_allowed(
            target, connector_id=self.connector_id, tool_name=self.tool_name
        )


ConnectorHandler = Callable[[ConnectorInvocation], ConnectorResult | dict[str, Any]]


@dataclass(frozen=True)
class RegisteredConnector:
    manifest: ConnectorManifest
    handler: ConnectorHandler


class ConnectorRegistry:
    """Maps connector ids and connector tool names to manifest + handler."""

    def __init__(self) -> None:
        self._connectors: dict[str, RegisteredConnector] = {}
        self._tool_owner: dict[str, str] = {}

    def register(self, manifest: ConnectorManifest, handler: ConnectorHandler) -> None:
        existing = self._connectors.get(manifest.connector_id)
        if existing is not None and existing.manifest != manifest:
            raise ConnectorPolicyDenied(
                f"Connector {manifest.connector_id!r} is already registered with a "
                "different manifest",
                connector_id=manifest.connector_id,
            )
        for tool_name in manifest.tool_names:
            owner = self._tool_owner.get(tool_name)
            if owner is not None and owner != manifest.connector_id:
                raise ConnectorPolicyDenied(
                    f"Tool {tool_name!r} is already provided by connector {owner!r}",
                    connector_id=manifest.connector_id,
                    tool_name=tool_name,
                )
        self._connectors[manifest.connector_id] = RegisteredConnector(
            manifest=manifest, handler=handler
        )
        for tool_name in manifest.tool_names:
            self._tool_owner[tool_name] = manifest.connector_id

    def get(self, connector_id: str) -> RegisteredConnector:
        try:
            return self._connectors[connector_id]
        except KeyError:
            raise ConnectorPolicyDenied(
                f"Unregistered connector {connector_id!r}",
                connector_id=connector_id,
                details={"known_connectors": sorted(self._connectors)},
            ) from None

    def for_tool(self, tool_name: str) -> RegisteredConnector:
        connector_id = self._tool_owner.get(tool_name)
        if connector_id is None:
            raise ConnectorPolicyDenied(
                f"No connector provides tool {tool_name!r}",
                tool_name=tool_name,
                details={"known_tools": sorted(self._tool_owner)},
            )
        return self._connectors[connector_id]

    def handles(self, tool_name: str) -> bool:
        return tool_name in self._tool_owner

    def connector_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._connectors))

    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tool_owner)

    def manifests(self) -> tuple[ConnectorManifest, ...]:
        return tuple(entry.manifest for entry in self._connectors.values())

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        definitions: list[ToolDefinition] = []
        for entry in self._connectors.values():
            definitions.extend(entry.manifest.tool_definitions())
        return tuple(definitions)

    def tool_class_for(self, tool_name: str) -> str | None:
        connector_id = self._tool_owner.get(tool_name)
        if connector_id is None:
            return None
        return self._connectors[connector_id].manifest.tool_class

    def tool_names_by_class(self) -> dict[str, frozenset[str]]:
        """Connector tool names grouped by tool class."""
        grouped: dict[str, set[str]] = {}
        for entry in self._connectors.values():
            grouped.setdefault(entry.manifest.tool_class, set()).update(entry.manifest.tool_names)
        return {tool_class: frozenset(names) for tool_class, names in grouped.items()}

    def tool_names_for_classes(self, tool_classes: Iterable[str]) -> frozenset[str]:
        wanted = set(tool_classes)
        return frozenset(
            tool_name
            for tool_name, connector_id in self._tool_owner.items()
            if self._connectors[connector_id].manifest.tool_class in wanted
        )

    def __iter__(self) -> Iterator[RegisteredConnector]:
        return iter(self._connectors.values())

    def __len__(self) -> int:
        return len(self._connectors)


__all__ = [
    "ConnectorHandler",
    "ConnectorInvocation",
    "ConnectorRegistry",
    "RegisteredConnector",
]
