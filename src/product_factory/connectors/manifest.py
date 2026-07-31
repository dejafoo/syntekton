"""Connector manifests — what a connector is allowed to do, declared up front.

A manifest is the whole policy surface for one provider. Nothing about a
connector is inferred at call time: the tools it exposes, whether it may write,
which hosts it may reach, which environment variable holds its credential, and
how much of a result may be retained are all declared here and checked before
dispatch.

Manifests never grant anything. A connector tool still has to be in the task's
`CapabilityGrant.tool_names` to run, exactly like `read_file`
(see ADR-004 — `ToolBroker` remains the sole execution path).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from product_factory.connectors.errors import ConnectorEgressDenied
from product_factory.domain.tools import RiskClass, ToolDefinition

Permission = Literal["read", "write", "destructive"]

# How much of a provider response may be persisted in events/artifacts.
ResultRetention = Literal["none", "hash_only", "excerpt", "full"]

EgressMode = Literal["none", "domains"]


def host_of(target: str) -> str:
    """Extract a comparable hostname from a URL, `host:port`, or bare host."""
    raw = (target or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    raw = raw.rsplit("@", 1)[-1]
    # Strip a port, but leave bracketed IPv6 literals intact.
    if raw.startswith("["):
        return raw
    return raw.split(":", 1)[0]


def domain_matches(host: str, pattern: str) -> bool:
    """Whether `host` falls under `pattern`, matching whole labels only."""
    pattern = pattern.strip().lower().lstrip(".")
    if not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        pattern = pattern[2:]
    # A bare domain also covers its subdomains. Label-wise comparison means
    # `tavily.com` never matches `tavily.com.attacker.net`.
    return host == pattern or host.endswith(f".{pattern}")


class EgressPolicy(BaseModel):
    """Which network hosts a connector may reach.

    `mode="none"` means no network at all — the correct setting for connectors
    that talk to a local subprocess. Denies are checked before allows so a
    denied host cannot be re-admitted by a broad allow entry.
    """

    model_config = {"frozen": True}

    mode: EgressMode = "none"
    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _domains_require_mode(self) -> EgressPolicy:
        if self.mode == "domains" and not self.allowed_domains:
            raise ValueError("egress mode 'domains' requires at least one allowed domain")
        if self.mode == "none" and self.allowed_domains:
            raise ValueError("egress mode 'none' cannot declare allowed domains")
        return self

    def assert_allowed(self, target: str, *, connector_id: str = "", tool_name: str = "") -> str:
        """Return the host of `target` or raise `ConnectorEgressDenied`."""
        host = host_of(target)
        if not host:
            raise ConnectorEgressDenied(
                f"Connector {connector_id or '?'} egress target is empty",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"target": target},
            )
        if self.mode == "none":
            raise ConnectorEgressDenied(
                f"Connector {connector_id or '?'} declares no network egress",
                connector_id=connector_id,
                tool_name=tool_name,
                details={"target": target, "host": host},
            )
        for pattern in self.denied_domains:
            if domain_matches(host, pattern):
                raise ConnectorEgressDenied(
                    f"Host {host} is on the deny list for {connector_id or '?'}",
                    connector_id=connector_id,
                    tool_name=tool_name,
                    details={"host": host, "pattern": pattern},
                )
        for pattern in self.allowed_domains:
            if domain_matches(host, pattern):
                return host
        raise ConnectorEgressDenied(
            f"Host {host} is not in the egress allowlist for {connector_id or '?'}",
            connector_id=connector_id,
            tool_name=tool_name,
            details={"host": host, "allowed_domains": list(self.allowed_domains)},
        )


class ConnectorToolSpec(BaseModel):
    """One tool a connector exposes, under a Product Factory tool name."""

    model_config = {"frozen": True}

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    # Upstream name when it differs from the PF name (e.g. an MCP server's
    # `read_text_file` surfaced as `mcp_read_file`).
    remote_name: str | None = None
    permissions: frozenset[Permission] = frozenset({"read"})
    risk_class: RiskClass = "R1"
    requires_approval: bool = False
    timeout_seconds: int | None = None
    idempotent: bool = True

    @field_validator("name")
    @classmethod
    def _name_is_identifier(cls, value: str) -> str:
        raw = value.strip()
        if not raw.replace("_", "").isalnum():
            raise ValueError(f"Connector tool name must be alphanumeric/underscore, got {value!r}")
        return raw

    @property
    def upstream_name(self) -> str:
        return self.remote_name or self.name

    def as_tool_definition(self, *, tool_class: str, timeout_seconds: int) -> ToolDefinition:
        """Metadata for the shared `ToolRegistry`.

        `result_may_be_untrusted` is hardcoded True: everything crossing a
        connector boundary is third-party text.
        """
        return ToolDefinition(
            name=self.name,
            description=self.description,
            tool_class=tool_class,
            input_schema=self.input_schema
            or {"type": "object", "properties": {}, "additionalProperties": True},
            output_schema=self.output_schema,
            risk_class=self.risk_class,
            resource_scope="connector",
            idempotent=self.idempotent,
            timeout_seconds=self.timeout_seconds or timeout_seconds,
            requires_human_approval=self.requires_approval,
            result_may_be_untrusted=True,
        )


class ConnectorManifest(BaseModel):
    """Immutable declaration of one connector's identity, tools, and limits."""

    model_config = {"frozen": True}

    connector_id: str
    version: str
    provider: str
    tool_class: str
    tools: tuple[ConnectorToolSpec, ...]
    risk_class: RiskClass = "R1"
    permissions: frozenset[Permission] = frozenset({"read"})
    egress: EgressPolicy = EgressPolicy()
    # Name of the environment variable holding the credential — never the value.
    auth_env_var: str | None = None
    timeout_seconds: int = 30
    max_concurrency: int = 2
    requires_approval: bool = False
    result_retention: ResultRetention = "excerpt"
    max_result_bytes: int = 64_000
    description: str = ""

    @field_validator("tools")
    @classmethod
    def _tools_present_and_unique(
        cls, value: tuple[ConnectorToolSpec, ...]
    ) -> tuple[ConnectorToolSpec, ...]:
        if not value:
            raise ValueError("A connector manifest must declare at least one tool")
        names = [tool.name for tool in value]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate connector tool names: {duplicates}")
        return value

    @model_validator(mode="after")
    def _tool_permissions_within_connector(self) -> ConnectorManifest:
        for tool in self.tools:
            extra = tool.permissions - self.permissions
            if extra:
                raise ValueError(
                    f"Tool {tool.name!r} requests {sorted(extra)} beyond connector "
                    f"permissions {sorted(self.permissions)}"
                )
        return self

    @property
    def read_only(self) -> bool:
        return self.permissions <= {"read"}

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)

    def tool(self, name: str) -> ConnectorToolSpec | None:
        for spec in self.tools:
            if spec.name == name:
                return spec
        return None

    def timeout_for(self, tool: ConnectorToolSpec) -> int:
        return tool.timeout_seconds or self.timeout_seconds

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            tool.as_tool_definition(
                tool_class=self.tool_class, timeout_seconds=self.timeout_seconds
            )
            for tool in self.tools
        )

    def as_payload(self) -> dict[str, Any]:
        """Audit-safe description. Includes the credential's variable name, never its value."""
        return {
            "connector_id": self.connector_id,
            "version": self.version,
            "provider": self.provider,
            "tool_class": self.tool_class,
            "risk_class": self.risk_class,
            "permissions": sorted(self.permissions),
            "egress_mode": self.egress.mode,
            "egress_allowed_domains": list(self.egress.allowed_domains),
            "auth_env_var": self.auth_env_var,
            "requires_approval": self.requires_approval,
            "result_retention": self.result_retention,
            "tools": [tool.name for tool in self.tools],
        }


__all__ = [
    "ConnectorManifest",
    "ConnectorToolSpec",
    "EgressPolicy",
    "Permission",
    "ResultRetention",
    "domain_matches",
    "host_of",
]
