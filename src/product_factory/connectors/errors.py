"""Typed connector failures.

None of these derive from `ProviderError`/`RuntimeFailureError`. That is
deliberate: the model gateway falls back to another provider on `ProviderError`,
and a connector outage is not a model failure. Retrying a different model
against a dead Tavily endpoint or a crashed MCP server burns budget and hides
the real cause, so connector problems surface as themselves.
"""

from __future__ import annotations

from product_factory.domain.errors import ProductFactoryError, UnsafeOperationError


class ConnectorError(ProductFactoryError):
    """Base for every connector failure."""

    exit_code = 10

    def __init__(
        self,
        message: str,
        *,
        connector_id: str = "",
        tool_name: str = "",
        details: dict | None = None,
    ) -> None:
        merged = dict(details or {})
        if connector_id:
            merged.setdefault("connector_id", connector_id)
        if tool_name:
            merged.setdefault("tool_name", tool_name)
        super().__init__(message, details=merged)
        self.connector_id = connector_id
        self.tool_name = tool_name

    @property
    def denial_code(self) -> str:
        return "connector_error"


class ConnectorPolicyDenied(ConnectorError, UnsafeOperationError):
    """Policy refused the invocation: not allowlisted, not granted, wrong permission class.

    Also an `UnsafeOperationError` so existing fail-closed handling that keys off
    that base treats a connector denial the same as a broker authorization denial.
    """

    exit_code = 8

    @property
    def denial_code(self) -> str:
        return "policy_denied"


class ConnectorEgressDenied(ConnectorPolicyDenied):
    """The connector tried to reach a host outside its declared egress allowlist."""

    exit_code = 8

    @property
    def denial_code(self) -> str:
        return "egress_denied"


class ConnectorUnavailable(ConnectorError):
    """The provider could not be reached, is unconfigured, or returned garbage."""

    exit_code = 10

    @property
    def denial_code(self) -> str:
        return "unavailable"


class ConnectorTimeout(ConnectorUnavailable):
    """The provider exceeded the manifest timeout."""

    exit_code = 10

    @property
    def denial_code(self) -> str:
        return "timeout"


__all__ = [
    "ConnectorEgressDenied",
    "ConnectorError",
    "ConnectorPolicyDenied",
    "ConnectorTimeout",
    "ConnectorUnavailable",
]
