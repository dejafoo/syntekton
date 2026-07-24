"""Loopback-first auth helpers."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, Request

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
    Loopback is open by default.
    Non-loopback requires PRODUCT_FACTORY_OBSERVE_TOKEN bearer auth.
    """
    client = request.client.host if request.client else ""
    if client in _LOOPBACK_HOSTS:
        return
    token = os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN")
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Non-loopback binding requires PRODUCT_FACTORY_OBSERVE_TOKEN",
        )
    _check_bearer(authorization, token)


def require_write_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    Control (write) routes: when PRODUCT_FACTORY_OBSERVE_TOKEN is set, require
    bearer auth even on loopback. When unset, same rules as require_auth.
    """
    token = os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN")
    if token:
        _check_bearer(authorization, token)
        return
    require_auth(request, authorization)
