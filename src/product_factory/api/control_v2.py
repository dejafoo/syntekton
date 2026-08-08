"""Write/control routes for product-factory.host/v2 (/api/v2)."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from product_factory.api.auth import enforce_submit_rate_limit, require_write_auth
from product_factory.api.deps import ApiState
from product_factory.api.remote_mode import (
    canonical_observe_base,
    remote_mode_enabled,
    repositories_for_root,
)
from product_factory.domain.budgets import run_budget_from_policy
from product_factory.domain.runs import RunRequest, WorkspaceProvenance
from product_factory.host.bounds import (
    BoundViolation,
    enforce_metadata,
    enforce_note,
    enforce_pack_input,
    enforce_request_text,
)
from product_factory.host.handoff_claims import claims_to_handoff_refs
from product_factory.host.protocol import HostResponse
from product_factory.host.protocol_v2 import (
    HOST_PROTOCOL_V2,
    ApproveV2Body,
    HandoffSupersedeV2Body,
    HostResponseV2,
    ReviseV2Body,
    SubmitRunV2Body,
    protocol_metadata,
)
from product_factory.trust.handoffs import HandoffRefusal

router = APIRouter(prefix="/api/v2", dependencies=[Depends(require_write_auth)])


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


def _observe_base(request: Request) -> str:
    return canonical_observe_base(request_base=str(request.base_url))


def _operator_actor() -> dict[str, str]:
    return {"kind": "local_operator", "id": "api_operator"}


def _debug_mode_enabled() -> bool:
    return os.environ.get("PRODUCT_FACTORY_FORCE_MOCK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    } or os.environ.get("PRODUCT_FACTORY_HOST_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _v2_json(response: HostResponseV2, *, success_status: int = 200) -> JSONResponse:
    status = success_status
    if not response.ok and response.error is not None:
        code = response.error.code
        if code == "not_found":
            status = 404
        elif code in {"plan_rejected", "invalid_pack_input", "invalid_handoff", "bound_violation"}:
            status = 422
        else:
            status = 400
    return JSONResponse(content=response.model_dump(mode="json"), status_code=status)


def _from_v1(operation: str, response: HostResponse) -> HostResponseV2:
    if not response.ok:
        err = response.error
        return HostResponseV2.failure(
            operation=operation,
            code=err.code if err else "error",
            message=err.message if err else "request failed",
            run_id=response.run_id,
            status=response.status,
            details=err.details if err else {},
        )
    result: dict[str, Any] = {}
    if response.plan_summary is not None:
        result["plan_summary"] = response.plan_summary
    if response.artifacts:
        result["artifacts"] = response.artifacts
    if response.events:
        result["events"] = response.events
    if response.data:
        result.update(response.data)
    return HostResponseV2.success(
        operation=operation,
        run_id=response.run_id,
        status=response.status,
        result=result,
        subscription=response.subscription,
    )


def _resolve_repository(
    body: SubmitRunV2Body, *, project_root: Path, workspace_root: Path
) -> tuple[Path | None, str | None, WorkspaceProvenance | None, HostResponseV2 | None]:
    if remote_mode_enabled() and body.repository_path is not None:
        return (
            None,
            None,
            None,
            HostResponseV2.failure(
                operation="submit",
                code="repository_path_forbidden",
                message="repository_path is not accepted in remote mode; use repository_id",
            ),
        )
    if body.repository_id is not None:
        repos = repositories_for_root(project_root)
        try:
            path = repos.resolve(body.repository_id)
        except Exception as exc:
            return (
                None,
                None,
                None,
                HostResponseV2.failure(
                    operation="submit",
                    code="unknown_repository",
                    message=str(exc),
                ),
            )
        return path, body.repository_id, None, None
    if body.repository_path is not None:
        return Path(body.repository_path).expanduser().resolve(), None, None, None
    return None, None, None, None


@router.get("/meta")
def meta_v2(request: Request) -> dict[str, Any]:
    state = _state(request)
    h = state.query.health()
    meta = protocol_metadata()
    meta.update(
        {
            "protocol": HOST_PROTOCOL_V2,
            "api_version": "v2",
            "schema_version": 2,
            "latest_seq": h.latest_seq,
            "capture_level": h.capture_level,
            "wal_mode": h.wal_mode,
            "remote_mode": remote_mode_enabled(),
            "canonical_observe_base": _observe_base(request),
        }
    )
    return meta


@router.post("/runs")
def submit_run_v2(body: SubmitRunV2Body, request: Request) -> JSONResponse:
    """Submit via host/v2. Debug modes (mock/inline/sync) are server-config only."""
    enforce_submit_rate_limit(request)
    try:
        enforce_request_text(body.request_text)
        enforce_pack_input(body.pack_input)
        enforce_metadata(body.metadata)
    except BoundViolation as exc:
        return _v2_json(
            HostResponseV2.failure(
                operation="submit",
                code="bound_violation",
                message=exc.message,
                details=exc.details,
            )
        )

    host = _state(request).host(
        mock=_debug_mode_enabled(),
        observe_base_url=_observe_base(request),
    )
    repo_path, repo_id, workspace_provenance, err = _resolve_repository(
        body,
        project_root=host.config.root,
        workspace_root=host.pf_root / "workspaces",
    )
    if err is not None:
        return _v2_json(err)

    try:
        handoff_refs = claims_to_handoff_refs(host.coord.db, list(body.handoffs))
    except HandoffRefusal as exc:
        return _v2_json(
            HostResponseV2.failure(
                operation="submit",
                code="invalid_handoff",
                message=str(exc),
            )
        )

    run_request = RunRequest(
        request_id=body.request_id or f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=body.workflow_type,
        request_text=body.request_text,
        repository_path=repo_path,
        repository_id=repo_id,
        workspace_provenance=workspace_provenance,
        validation_commands=list(body.validation_commands),
        artifact_overrides=dict(body.artifact_overrides),
        pack_input=dict(body.pack_input),
        handoff_refs=handoff_refs,
        metadata=dict(body.metadata),
        budget=run_budget_from_policy(
            max_cost_usd=Decimal(str(body.budget_usd)),
            budgets=host.config.policies.budgets,
            max_wall_clock_seconds=body.max_wall_clock_seconds,
        ),
    )
    # Server/test configuration owns mock/inline/sync — never the request body.
    response = host.submit(
        run_request,
        mock=_debug_mode_enabled(),
        detach=True,
        inline_thread=False,
    )
    return _v2_json(_from_v1("submit", response), success_status=202)


@router.get("/runs/{run_id}")
def status_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "status",
            _state(request).host(observe_base_url=_observe_base(request)).status(run_id),
        )
    )


@router.get("/runs/{run_id}/inspect")
def inspect_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "inspect",
            _state(request).host(observe_base_url=_observe_base(request)).inspect(run_id),
        )
    )


@router.post("/runs/{run_id}/approve")
def approve_v2(run_id: str, body: ApproveV2Body, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "approve",
            _state(request)
            .host(observe_base_url=_observe_base(request))
            .approve(run_id, apply=body.apply),
        )
    )


@router.post("/runs/{run_id}/reject")
def reject_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "reject",
            _state(request).host(observe_base_url=_observe_base(request)).reject(run_id),
        )
    )


@router.post("/runs/{run_id}/cancel")
def cancel_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "cancel",
            _state(request).host(observe_base_url=_observe_base(request)).cancel(run_id),
        )
    )


@router.post("/runs/{run_id}/revise")
def revise_v2(run_id: str, body: ReviseV2Body, request: Request) -> JSONResponse:
    try:
        enforce_note(body.note)
    except BoundViolation as exc:
        return _v2_json(
            HostResponseV2.failure(
                operation="revise",
                code="bound_violation",
                message=exc.message,
                details=exc.details,
                run_id=run_id,
            )
        )
    return _v2_json(
        _from_v1(
            "revise",
            _state(request)
            .host(observe_base_url=_observe_base(request))
            .revise(run_id, note=body.note),
        )
    )


@router.post("/runs/{run_id}/resume")
def resume_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "resume",
            _state(request).host(observe_base_url=_observe_base(request)).resume(run_id),
        )
    )


@router.post("/runs/{run_id}/apply")
def apply_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "apply",
            _state(request).host(observe_base_url=_observe_base(request)).apply(run_id),
        )
    )


@router.get("/runs/{run_id}/handoffs")
def list_handoffs_v2(run_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "handoffs",
            _state(request).host(observe_base_url=_observe_base(request)).handoffs(run_id),
        )
    )


@router.post("/handoffs/{handoff_id}/approve")
def approve_handoff_v2(handoff_id: str, request: Request) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "approve_handoff",
            _state(request)
            .host(observe_base_url=_observe_base(request))
            .approve_handoff(handoff_id, actor=_operator_actor()),
        )
    )


@router.post("/handoffs/{handoff_id}/supersede")
def supersede_handoff_v2(
    handoff_id: str, body: HandoffSupersedeV2Body, request: Request
) -> JSONResponse:
    return _v2_json(
        _from_v1(
            "supersede_handoff",
            _state(request)
            .host(observe_base_url=_observe_base(request))
            .supersede_handoff(
                handoff_id,
                successor_handoff_id=body.successor_handoff_id,
                actor=_operator_actor(),
            ),
        )
    )


@router.post("/plan")
def plan_preview_v2(body: SubmitRunV2Body, request: Request) -> JSONResponse:
    """Plan preview uses the same strict body as submit (minus execution)."""
    try:
        enforce_request_text(body.request_text)
        enforce_pack_input(body.pack_input)
    except BoundViolation as exc:
        return _v2_json(
            HostResponseV2.failure(
                operation="plan",
                code="bound_violation",
                message=exc.message,
                details=exc.details,
            )
        )
    host = _state(request).host(
        mock=_debug_mode_enabled(),
        observe_base_url=_observe_base(request),
    )
    return _v2_json(
        _from_v1(
            "plan",
            host.plan_preview(
                request_text=body.request_text,
                workflow_type=body.workflow_type,
            ),
        )
    )
