"""Loopback-first auth helpers."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException, Request


def require_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """
    Loopback is open by default.
    Non-loopback requires PRODUCT_FACTORY_OBSERVE_TOKEN bearer auth.
    """
    client = request.client.host if request.client else ""
    if client in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return
    token = os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN")
    if not token:
        raise HTTPException(
            status_code=403,
            detail="Non-loopback binding requires PRODUCT_FACTORY_OBSERVE_TOKEN",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    provided = authorization.split(" ", 1)[1].strip()
    if not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid token")
