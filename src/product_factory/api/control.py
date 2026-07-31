"""Write/control routes for the local host API (P3.B)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from product_factory.api.auth import require_write_auth
from product_factory.api.deps import ApiState
from product_factory.domain.budgets import run_budget_from_policy
from product_factory.domain.runs import ArtifactOverride, RunRequest, WorkflowType
from product_factory.host.protocol import HostResponse

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_write_auth)])


class SubmitRunBody(BaseModel):
    request_text: str
    workflow_type: WorkflowType = "code_change"
    repository_path: Path | None = None
    model_profile_set: str = "local-target"
    validation_commands: list[str] = Field(default_factory=list)
    artifact_overrides: dict[str, ArtifactOverride] = Field(default_factory=dict)
    pack_input: dict[str, Any] = Field(default_factory=dict)
    budget_usd: float = 3.0
    max_wall_clock_seconds: int | None = None
    request_id: str | None = None
    mock: bool = False
    inline: bool = False
    sync: bool = False


class ApproveBody(BaseModel):
    apply: bool = False


class ReviseBody(BaseModel):
    note: str = ""


class MaterializeBody(BaseModel):
    artifact: str
    dest_path: str
    overwrite: bool = False


class MaterializeAllBody(BaseModel):
    roles: list[str] = Field(default_factory=list)
    overwrite: bool = False


class PlanPreviewBody(BaseModel):
    request_text: str
    workflow_type: WorkflowType = "code_change"
    mock: bool = False


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


def _observe_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _host_json(response: HostResponse, *, success_status: int = 200) -> JSONResponse:
    status = success_status
    if not response.ok and response.error is not None:
        code = response.error.code
        if code == "not_found":
            status = 404
        elif code == "not_implemented":
            status = 501
        elif code == "plan_rejected":
            status = 422
        else:
            status = 400
    return JSONResponse(content=response.model_dump(mode="json"), status_code=status)


def _run_request(body: SubmitRunBody, *, budgets: Any = None) -> RunRequest:
    return RunRequest(
        request_id=body.request_id or f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=body.workflow_type,
        request_text=body.request_text,
        repository_path=body.repository_path.resolve() if body.repository_path else None,
        model_profile_set=body.model_profile_set,
        validation_commands=list(body.validation_commands),
        artifact_overrides=dict(body.artifact_overrides),
        pack_input=dict(body.pack_input),
        budget=run_budget_from_policy(
            max_cost_usd=Decimal(str(body.budget_usd)),
            budgets=budgets,
            max_wall_clock_seconds=body.max_wall_clock_seconds,
        ),
    )


@router.post("/runs")
def submit_run(body: SubmitRunBody, request: Request) -> JSONResponse:
    """Submit a curated request; returns run_id + SSE subscription immediately."""
    host = _state(request).host(mock=body.mock, observe_base_url=_observe_base(request))
    response = host.submit(
        _run_request(body, budgets=host.config.policies.budgets),
        mock=body.mock,
        detach=not body.inline and not body.sync,
        inline_thread=body.inline and not body.sync,
    )
    return _host_json(response, success_status=202)


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, request: Request, body: ApproveBody = ApproveBody()) -> JSONResponse:
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.approve(run_id, apply=body.apply))


@router.post("/runs/{run_id}/reject")
def reject_run(run_id: str, request: Request) -> JSONResponse:
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.reject(run_id))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> JSONResponse:
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.cancel(run_id))


@router.post("/runs/{run_id}/revise")
def revise_run(run_id: str, request: Request, body: ReviseBody = ReviseBody()) -> JSONResponse:
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.revise(run_id, note=body.note))


@router.post("/runs/{run_id}/materialize")
def materialize_run(run_id: str, request: Request, body: MaterializeBody) -> JSONResponse:
    """Land a run artifact under the run's repository_path."""
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(
        host.materialize(
            run_id,
            artifact=body.artifact,
            dest_path=body.dest_path,
            overwrite=body.overwrite,
        )
    )


@router.post("/runs/{run_id}/materialize-all")
def materialize_run_all(
    run_id: str, request: Request, body: MaterializeAllBody = MaterializeAllBody()
) -> JSONResponse:
    """Land every resolved deliverable of a run at its suggested destination."""
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(
        host.materialize_all(
            run_id,
            roles=list(body.roles) or None,
            overwrite=body.overwrite,
        )
    )


@router.post("/plan")
def plan_preview(body: PlanPreviewBody, request: Request) -> JSONResponse:
    """Plan-preview: compile a plan without creating or executing a run."""
    host = _state(request).host(mock=body.mock, observe_base_url=_observe_base(request))
    return _host_json(host.plan_preview(body.request_text, workflow_type=body.workflow_type))
