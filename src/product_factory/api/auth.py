"""Loopback-first auth helpers."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request

from product_factory.api.remote_mode import configured_control_token

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _check_bearer(authorization: str | None, token: str) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid token")


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    When a control/observe token is configured, require Bearer auth for all
    observe reads (including loopback). When unset, loopback stays open and
    non-loopback is refused.
    """
    token = configured_control_token()
    if token:
        _check_bearer(authorization, token)
        return
    client = request.client.host if request.client else ""
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
    token = configured_control_token()
    if token:
        _check_bearer(authorization, token)
        return
    require_auth(request, authorization)
