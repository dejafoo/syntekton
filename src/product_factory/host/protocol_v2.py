"""product-factory.host/v2 envelope and typed mutation payloads.

v1 (`product-factory.host/v1`) remains the compatibility adapter through the
v0.2 rollout. New clients should prefer v2.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from product_factory.domain.runs import ArtifactOverride
from product_factory.host.protocol import HostError, HostSubscription

HOST_PROTOCOL_V2: Literal["product-factory.host/v2"] = "product-factory.host/v2"
HOST_PROTOCOL_V1: Literal["product-factory.host/v1"] = "product-factory.host/v1"

DEFAULT_PROTOCOL = HOST_PROTOCOL_V2
SUPPORTED_PROTOCOLS: tuple[str, ...] = (HOST_PROTOCOL_V2, HOST_PROTOCOL_V1)

# Rollout calendar (v0.2 retains v1; v0.3 prefers v2 with durable warnings; v0.4 removes v1).
V1_DEPRECATION_DATE = date(2026, 12, 1)
V1_REMOVAL_DATE = date(2027, 3, 1)


class HandoffClaim(BaseModel):
    """Authority-shaped handoff assertion for host/v2 (no fat producer claims)."""

    model_config = {"extra": "forbid"}

    handoff_id: str = Field(min_length=1, max_length=128)
    expected_digest: str = Field(min_length=64, max_length=64)

    @field_validator("expected_digest")
    @classmethod
    def _hex_digest(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
            raise ValueError("expected_digest must be a 64-character hex SHA-256 digest")
        return value.lower()


class HostResponseV2(BaseModel):
    """Typed host/v2 response. Payload lives in `result` rather than a free-form bag."""

    protocol: Literal["product-factory.host/v2"] = HOST_PROTOCOL_V2
    ok: bool
    operation: str
    run_id: str | None = None
    status: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    subscription: HostSubscription | None = None
    error: HostError | None = None

    @classmethod
    def success(
        cls,
        *,
        operation: str,
        run_id: str | None = None,
        status: str | None = None,
        result: dict[str, Any] | None = None,
        subscription: HostSubscription | None = None,
    ) -> HostResponseV2:
        return cls(
            ok=True,
            operation=operation,
            run_id=run_id,
            status=status,
            result=result or {},
            subscription=subscription,
        )

    @classmethod
    def failure(
        cls,
        *,
        operation: str,
        code: str,
        message: str,
        run_id: str | None = None,
        status: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> HostResponseV2:
        return cls(
            ok=False,
            operation=operation,
            run_id=run_id,
            status=status,
            error=HostError(code=code, message=message, details=details or {}),
        )


class SubmitRunV2Body(BaseModel):
    """Strict submit body for host/v2 and /api/v2."""

    model_config = {"extra": "forbid"}

    request_text: str
    workflow_type: str = "code_change"
    repository_path: str | None = None
    repository_id: str | None = None
    validation_commands: list[str] = Field(default_factory=list, max_length=32)
    artifact_overrides: dict[str, ArtifactOverride] = Field(default_factory=dict)
    pack_input: dict[str, Any] = Field(default_factory=dict)
    handoffs: list[HandoffClaim] = Field(default_factory=list, max_length=32)
    budget_usd: float = Field(default=3.0, gt=0, le=10_000)
    max_wall_clock_seconds: int | None = Field(default=None, ge=1, le=86400)
    request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ApproveV2Body(BaseModel):
    model_config = {"extra": "forbid"}
    apply: bool = False


class ReviseV2Body(BaseModel):
    model_config = {"extra": "forbid"}
    note: str = ""


class HandoffSupersedeV2Body(BaseModel):
    model_config = {"extra": "forbid"}
    successor_handoff_id: str | None = None


def protocol_metadata() -> dict[str, Any]:
    """Advertised by /api/v1/meta and /api/v2/meta."""
    return {
        "supported_protocols": list(SUPPORTED_PROTOCOLS),
        "default_protocol": DEFAULT_PROTOCOL,
        "api_versions": ["v2", "v1"],
        "default_api_version": "v2",
        "deprecations": {
            "product-factory.host/v1": {
                "status": "supported_compatibility",
                "deprecation_date": V1_DEPRECATION_DATE.isoformat(),
                "removal_date": V1_REMOVAL_DATE.isoformat(),
                "replacement": HOST_PROTOCOL_V2,
            }
        },
        "dashboard": {
            "deployment_support": "loopback_monitor_only",
            "mutations": False,
            "bearer_token_storage": False,
            "remote_browser": "unsupported",
            "notes": (
                "Dashboard is loopback/monitor-only. A remote control token does not "
                "make the browser UI a public remote surface; use an operator-managed "
                "SSH/private tunnel to loopback if needed."
            ),
        },
        "debug_execution_modes": {
            "mock": "server_or_test_configuration_only",
            "inline": "server_or_test_configuration_only",
            "sync": "server_or_test_configuration_only",
        },
        "removed_v1_request_fields": [
            "project_profile",
            "model_profile_set",
            "requested_artifacts",
            "mock",
            "inline",
            "sync",
        ],
    }
