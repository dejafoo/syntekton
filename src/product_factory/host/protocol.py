"""Versioned JSON envelope for machine-facing host commands."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HOST_PROTOCOL: Literal["product-factory.host/v1"] = "product-factory.host/v1"


class HostError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class HostSubscription(BaseModel):
    sse_url: str | None = None
    cli_tail: str


class HostResponse(BaseModel):
    """Canonical machine response for `product-factory host` commands."""

    protocol: Literal["product-factory.host/v1"] = HOST_PROTOCOL
    ok: bool
    run_id: str | None = None
    status: str | None = None
    plan_summary: dict[str, Any] | None = None
    subscription: HostSubscription | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    data: dict[str, Any] | None = None
    error: HostError | None = None

    @classmethod
    def success(
        cls,
        *,
        run_id: str | None = None,
        status: str | None = None,
        plan_summary: dict[str, Any] | None = None,
        subscription: HostSubscription | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        events: list[dict[str, Any]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> HostResponse:
        return cls(
            ok=True,
            run_id=run_id,
            status=status,
            plan_summary=plan_summary,
            subscription=subscription,
            artifacts=artifacts or [],
            events=events or [],
            data=data,
        )

    @classmethod
    def failure(
        cls,
        *,
        code: str,
        message: str,
        run_id: str | None = None,
        status: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> HostResponse:
        return cls(
            ok=False,
            run_id=run_id,
            status=status,
            error=HostError(code=code, message=message, details=details or {}),
        )
