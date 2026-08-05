"""Bounded git-bundle upload routes (PM5.E)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from product_factory.api.auth import enforce_upload_rate_limit, require_write_auth
from product_factory.api.deps import ApiState
from product_factory.domain.errors import UnsafeOperationError
from product_factory.workspace.uploads import UploadPreflight, upload_bounds_summary

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_write_auth)])


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


def _error(exc: UnsafeOperationError, *, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "error": {
                "code": "upload_rejected",
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


@router.get("/uploads/bounds")
def upload_bounds(request: Request) -> dict[str, Any]:
    return upload_bounds_summary(_state(request).ingress_config())


@router.post("/uploads/git-bundle/preflight")
def upload_preflight(body: UploadPreflight, request: Request) -> JSONResponse:
    ctx = enforce_upload_rate_limit(request)
    store = _state(request).upload_store()
    try:
        session = store.preflight(body)
    except UnsafeOperationError as exc:
        auditor = ctx.get("auditor")
        if auditor is not None:
            auditor.emit(
                "ingress.upload_rejected",
                client_ip=str(ctx["client_ip"]),
                phase="preflight",
                reason=exc.message,
                details=exc.details,
            )
        return _error(exc)
    auditor = ctx.get("auditor")
    if auditor is not None:
        auditor.emit(
            "ingress.upload_preflight",
            client_ip=str(ctx["client_ip"]),
            upload_id=session.upload_id,
            declared_size=session.declared_size,
            declared_sha256=session.declared_sha256,
            media_type=session.media_type,
        )
    return JSONResponse(content=session.model_dump(mode="json"))


@router.put("/uploads/git-bundle/{upload_id}")
async def upload_body(upload_id: str, request: Request) -> JSONResponse:
    ctx = enforce_upload_rate_limit(request)
    config = _state(request).ingress_config()
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            size = -1
        if size < 0 or size > config.max_upload_bytes:
            return _error(
                UnsafeOperationError(
                    "Content-Length exceeds upload bound",
                    details={
                        "content_length": content_length,
                        "max_upload_bytes": config.max_upload_bytes,
                    },
                )
            )
    payload = await request.body()
    store = _state(request).upload_store()
    try:
        session = store.accept_bytes(upload_id, payload)
    except UnsafeOperationError as exc:
        auditor = ctx.get("auditor")
        if auditor is not None:
            auditor.emit(
                "ingress.upload_rejected",
                client_ip=str(ctx["client_ip"]),
                phase="accept",
                upload_id=upload_id,
                reason=exc.message,
                details=exc.details,
            )
        return _error(exc)
    auditor = ctx.get("auditor")
    if auditor is not None:
        auditor.emit(
            "ingress.upload_received",
            client_ip=str(ctx["client_ip"]),
            upload_id=session.upload_id,
            size_bytes=session.declared_size,
            sha256=session.declared_sha256,
        )
    return JSONResponse(content=session.model_dump(mode="json"))


@router.post("/uploads/git-bundle/{upload_id}/finalize")
def upload_finalize(upload_id: str, request: Request) -> JSONResponse:
    ctx = enforce_upload_rate_limit(request)
    store = _state(request).upload_store()
    try:
        finalized = store.finalize(upload_id)
    except UnsafeOperationError as exc:
        auditor = ctx.get("auditor")
        if auditor is not None:
            auditor.emit(
                "ingress.upload_rejected",
                client_ip=str(ctx["client_ip"]),
                phase="finalize",
                upload_id=upload_id,
                reason=exc.message,
                details=exc.details,
            )
        return _error(exc)
    auditor = ctx.get("auditor")
    if auditor is not None:
        auditor.emit(
            "ingress.upload_finalized",
            client_ip=str(ctx["client_ip"]),
            upload_id=finalized.upload_id,
            sha256=finalized.sha256,
            size_bytes=finalized.size_bytes,
            bundle_heads=finalized.bundle_heads,
        )
    return JSONResponse(
        content={
            "upload_id": finalized.upload_id,
            "sha256": finalized.sha256,
            "size_bytes": finalized.size_bytes,
            "media_type": finalized.media_type,
            "bundle_heads": finalized.bundle_heads,
            "status": "finalized",
        }
    )


@router.get("/uploads/git-bundle/{upload_id}")
def upload_status(upload_id: str, request: Request) -> Response:
    session = _state(request).upload_store().get(upload_id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "Upload not found"})
    return JSONResponse(content=session.model_dump(mode="json"))
