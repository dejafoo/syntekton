"""Run-scoped remote delivery endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from product_factory.api.auth import require_auth, require_write_auth
from product_factory.api.deps import ApiState
from product_factory.delivery.models import LandingReceipt
from product_factory.delivery.store import DeliveryError, DeliveryStore

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


def _store(request: Request) -> DeliveryStore:
    return DeliveryStore(_state(request).data_dir)


@router.get("/runs/{run_id}/delivery")
def delivery_manifest(run_id: str, request: Request) -> dict:
    row = _state(request).db.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        manifest = _store(request).build(run_id, row)
    except DeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return manifest.model_dump(mode="json")


@router.get("/runs/{run_id}/delivery/blobs/{sha256}")
def delivery_blob(run_id: str, sha256: str, request: Request) -> Response:
    try:
        content = _store(request).get_blob(run_id, sha256)
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Digest": f"sha-256={sha256}",
            "ETag": f'"sha256:{sha256}"',
            "Content-Disposition": f'attachment; filename="{sha256}"',
        },
    )


@router.post(
    "/runs/{run_id}/delivery/receipts",
    dependencies=[Depends(require_write_auth)],
    status_code=201,
)
def landing_receipt(run_id: str, body: LandingReceipt, request: Request) -> dict:
    try:
        return _store(request).append_receipt(run_id, body)
    except DeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
