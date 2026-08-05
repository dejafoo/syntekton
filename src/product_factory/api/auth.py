"""Loopback-first auth helpers with PM5.E rate limits and audit."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import Header, HTTPException, Request

from product_factory.api.ingress import (
    INGRESS_LIMITER,
    IngressAuditor,
    IngressConfig,
    load_ingress_config,
    resolve_client_ip,
)
from product_factory.api.remote_mode import configured_control_token

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _ingress_state(request: Request) -> tuple[IngressConfig, IngressAuditor | None]:
    state = getattr(request.app.state, "api_state", None)
    if state is None:
        return load_ingress_config(), None
    return state.ingress_config(), state.ingress_auditor()


def _check_bearer(
    authorization: str | None,
    token: str,
    *,
    request: Request,
    config: IngressConfig,
    auditor: IngressAuditor | None,
) -> None:
    client_ip = resolve_client_ip(request, config)
    if not authorization or not authorization.lower().startswith("bearer "):
        _record_auth_failure(
            auditor,
            client_ip=client_ip,
            reason="missing_bearer",
            config=config,
        )
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(provided, token):
        _record_auth_failure(
            auditor,
            client_ip=client_ip,
            reason="invalid_token",
            config=config,
        )
        raise HTTPException(status_code=401, detail="Invalid token")
    if auditor is not None:
        auditor.emit("ingress.auth_succeeded", client_ip=client_ip)


def _record_auth_failure(
    auditor: IngressAuditor | None,
    *,
    client_ip: str,
    reason: str,
    config: IngressConfig,
) -> None:
    # Separate failure bucket so invalid tokens trip faster than valid traffic.
    if not INGRESS_LIMITER.allow(
        bucket="auth_failure",
        client_ip=client_ip,
        limit=config.auth_failure_limit,
        window_seconds=config.auth_failure_window_seconds,
    ):
        if auditor is not None:
            auditor.emit(
                "ingress.rate_limited",
                client_ip=client_ip,
                bucket="auth_failure",
                reason=reason,
            )
        raise HTTPException(status_code=429, detail="Auth failure rate limit exceeded")
    if auditor is not None:
        auditor.emit("ingress.auth_failed", client_ip=client_ip, reason=reason)


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    When a control/observe token is configured, require Bearer auth for all
    observe reads (including loopback). When unset, loopback stays open and
    non-loopback is refused.
    """
    config, auditor = _ingress_state(request)
    token = configured_control_token()
    if token:
        _check_bearer(
            authorization,
            token,
            request=request,
            config=config,
            auditor=auditor,
        )
        return
    client = resolve_client_ip(request, config)
    if client in _LOOPBACK_HOSTS:
        return
    raise HTTPException(
        status_code=403,
        detail="Non-loopback binding requires PRODUCT_FACTORY_OBSERVE_TOKEN",
    )


def require_write_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    Control (write) routes: when a token is configured, require bearer auth even
    on loopback. When unset, same rules as require_auth.
    """
    config, auditor = _ingress_state(request)
    token = configured_control_token()
    if token:
        _check_bearer(
            authorization,
            token,
            request=request,
            config=config,
            auditor=auditor,
        )
        return
    require_auth(request, authorization)


def enforce_submit_rate_limit(request: Request) -> dict[str, Any]:
    """Rate-limit run submissions; returns audit context for the caller."""
    config, auditor = _ingress_state(request)
    client_ip = resolve_client_ip(request, config)
    if not INGRESS_LIMITER.allow(
        bucket="submit",
        client_ip=client_ip,
        limit=config.submit_rate_limit,
        window_seconds=config.submit_window_seconds,
    ):
        if auditor is not None:
            auditor.emit("ingress.rate_limited", client_ip=client_ip, bucket="submit")
        raise HTTPException(status_code=429, detail="Submit rate limit exceeded")
    if auditor is not None:
        auditor.emit("ingress.submit_accepted", client_ip=client_ip)
    return {"client_ip": client_ip}


def enforce_upload_rate_limit(request: Request) -> dict[str, Any]:
    config, auditor = _ingress_state(request)
    client_ip = resolve_client_ip(request, config)
    if not INGRESS_LIMITER.allow(
        bucket="upload",
        client_ip=client_ip,
        limit=config.upload_rate_limit,
        window_seconds=config.upload_window_seconds,
    ):
        if auditor is not None:
            auditor.emit("ingress.rate_limited", client_ip=client_ip, bucket="upload")
        raise HTTPException(status_code=429, detail="Upload rate limit exceeded")
    return {"client_ip": client_ip, "auditor": auditor, "config": config}
