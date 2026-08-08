"""Write/control routes for the local host API (P3.B)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from product_factory.api.auth import enforce_submit_rate_limit, require_write_auth
from product_factory.api.deps import ApiState
from product_factory.api.remote_mode import (
    canonical_observe_base,
    remote_mode_enabled,
    repositories_for_root,
)
from product_factory.delivery.store import DeliveryError, DeliveryStore
from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.budgets import run_budget_from_policy
from product_factory.domain.errors import ConfigurationError, ProductFactoryError
from product_factory.domain.runs import (
    ArtifactOverride,
    GitRefWorkspace,
    RunRequest,
    WorkflowType,
    WorkspaceProvenance,
)
from product_factory.host.protocol import HostResponse
from product_factory.workspace import WorkspaceManager

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_write_auth)])


class SubmitRunBody(BaseModel):
    request_text: str
    workflow_type: WorkflowType = "code_change"
    repository_path: Path | None = None
    repository_id: str | None = None
    workspace: GitRefWorkspace | None = None
    model_profile_set: str = "local-target"
    validation_commands: list[str] = Field(default_factory=list)
    artifact_overrides: dict[str, ArtifactOverride] = Field(default_factory=dict)
    pack_input: dict[str, Any] = Field(default_factory=dict)
    handoff_refs: list[HandoffRef] = Field(default_factory=list)
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


class HandoffSupersedeBody(BaseModel):
    successor_handoff_id: str | None = None


class ActionApprovalCreateBody(BaseModel):
    action_type: str
    subject_run_id: str
    action_fingerprint: str = Field(min_length=64, max_length=64)
    subject_artifact_instance_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


class ActionApprovalDecisionBody(BaseModel):
    decision: Literal["approved", "rejected"]


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


def _observe_base(request: Request) -> str:
    return canonical_observe_base(request_base=str(request.base_url))


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


def _operator_actor() -> dict[str, str]:
    return {"kind": "local_operator", "id": "api_operator"}


def _resolve_repository(
    body: SubmitRunBody, *, project_root: Path, workspace_root: Path
) -> tuple[Path | None, str | None, WorkspaceProvenance | None, HostResponse | None]:
    """Resolve repository_id / path under remote-mode rules.

    Returns (path, repository_id, workspace provenance, error_response).
    """
    if body.workspace is not None and (
        body.repository_path is not None or body.repository_id is not None
    ):
        return (
            None,
            None,
            None,
            HostResponse.failure(
                code="workspace_conflict",
                message="workspace cannot be combined with repository_path or repository_id",
            ),
        )

    if remote_mode_enabled() and body.repository_path is not None:
        return (
            None,
            None,
            None,
            HostResponse.failure(
                code="remote_repository_path_rejected",
                message=(
                    "Remote mode rejects client repository_path; "
                    "submit repository_id for a server-registered path, "
                    "or omit both for no-repo workflows"
                ),
                details={
                    "repository_path": str(body.repository_path),
                    "supported_workspace_kinds": ["none", "registered_path", "git_ref"],
                },
            ),
        )

    if body.workspace is not None:
        try:
            repos = repositories_for_root(project_root)
            prepared = WorkspaceManager(repos, workspace_root).prepare(
                body.workspace,
                workspace_id=f"workspace-{uuid.uuid4().hex}",
            )
            return (
                prepared.path,
                body.workspace.repository_id,
                prepared.provenance,
                None,
            )
        except ProductFactoryError as exc:
            return (
                None,
                None,
                None,
                HostResponse.failure(
                    code="invalid_workspace",
                    message=exc.message,
                    details=exc.details,
                ),
            )

    repository_id = body.repository_id
    if repository_id:
        try:
            repos = repositories_for_root(project_root)
            return repos.resolve(repository_id), repository_id, None, None
        except ConfigurationError as exc:
            return (
                None,
                None,
                None,
                HostResponse.failure(
                    code="unknown_repository_id",
                    message=exc.message,
                    details=exc.details,
                ),
            )

    path = body.repository_path.resolve() if body.repository_path else None
    return path, None, None, None


def _run_request(
    body: SubmitRunBody,
    *,
    budgets: Any = None,
    repository_path: Path | None = None,
    repository_id: str | None = None,
    workspace_provenance: WorkspaceProvenance | None = None,
) -> RunRequest:
    workspace = None
    if body.workspace is not None and workspace_provenance is not None:
        workspace = body.workspace.model_copy(update={"commit": workspace_provenance.commit})
    return RunRequest(
        request_id=body.request_id or f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=body.workflow_type,
        request_text=body.request_text,
        repository_path=repository_path,
        repository_id=repository_id,
        workspace=workspace,
        workspace_provenance=workspace_provenance,
        model_profile_set=body.model_profile_set,
        validation_commands=list(body.validation_commands),
        artifact_overrides=dict(body.artifact_overrides),
        pack_input=dict(body.pack_input),
        handoff_refs=list(body.handoff_refs),
        budget=run_budget_from_policy(
            max_cost_usd=Decimal(str(body.budget_usd)),
            budgets=budgets,
            max_wall_clock_seconds=body.max_wall_clock_seconds,
        ),
    )


@router.post("/runs")
def submit_run(body: SubmitRunBody, request: Request) -> JSONResponse:
    """Submit a curated request; returns run_id + SSE subscription immediately."""
    enforce_submit_rate_limit(request)
    host = _state(request).host(mock=body.mock, observe_base_url=_observe_base(request))
    repo_path, repo_id, workspace_provenance, err = _resolve_repository(
        body,
        project_root=host.config.root,
        workspace_root=host.pf_root / "workspaces",
    )
    if err is not None:
        return _host_json(err)
    response = host.submit(
        _run_request(
            body,
            budgets=host.config.policies.budgets,
            repository_path=repo_path,
            repository_id=repo_id,
            workspace_provenance=workspace_provenance,
        ),
        mock=body.mock,
        detach=not body.inline and not body.sync,
        inline_thread=body.inline and not body.sync,
    )
    return _host_json(response, success_status=202)


@router.get("/runs/{run_id}/handoffs")
def list_handoffs(run_id: str, request: Request) -> JSONResponse:
    return _host_json(_state(request).host(observe_base_url=_observe_base(request)).handoffs(run_id))


@router.post("/handoffs/{handoff_id}/approve")
def approve_handoff(handoff_id: str, request: Request) -> JSONResponse:
    return _host_json(
        _state(request).host(observe_base_url=_observe_base(request)).approve_handoff(
            handoff_id, actor=_operator_actor()
        )
    )


@router.post("/handoffs/{handoff_id}/supersede")
def supersede_handoff(
    handoff_id: str, body: HandoffSupersedeBody, request: Request
) -> JSONResponse:
    return _host_json(
        _state(request).host(observe_base_url=_observe_base(request)).supersede_handoff(
            handoff_id,
            successor_handoff_id=body.successor_handoff_id,
            actor=_operator_actor(),
        )
    )


@router.post("/action-approvals")
def create_action_approval(body: ActionApprovalCreateBody, request: Request) -> JSONResponse:
    from product_factory.trust.approvals import ApprovalError, ApprovalService

    try:
        approval = ApprovalService(_state(request).db).create_pending(
            action_type=body.action_type,
            subject_run_id=body.subject_run_id,
            subject_artifact_instance_id=body.subject_artifact_instance_id,
            action_fingerprint=body.action_fingerprint,
            actor=_operator_actor(),
            payload=body.payload,
            expires_at=body.expires_at,
        )
    except ApprovalError as exc:
        return _host_json(HostResponse.failure(code="invalid_approval", message=str(exc)))
    return JSONResponse(content=approval.model_dump(mode="json"), status_code=201)


@router.get("/action-approvals/{approval_id}")
def get_action_approval(approval_id: str, request: Request) -> JSONResponse:
    from product_factory.trust.approvals import ApprovalService

    approval = ApprovalService(_state(request).db).get(approval_id)
    if approval is None:
        return _host_json(HostResponse.failure(code="not_found", message="Unknown action approval"))
    return JSONResponse(content=approval.model_dump(mode="json"))


@router.post("/action-approvals/{approval_id}/decision")
def decide_action_approval(
    approval_id: str, body: ActionApprovalDecisionBody, request: Request
) -> JSONResponse:
    from product_factory.trust.approvals import ApprovalError, ApprovalService

    try:
        approval = ApprovalService(_state(request).db).decide(
            approval_id, body.decision, _operator_actor()
        )
    except ApprovalError as exc:
        return _host_json(HostResponse.failure(code="invalid_approval", message=str(exc)))
    return JSONResponse(content=approval.model_dump(mode="json"))


@router.get("/runs/{run_id}/status")
def run_status(run_id: str, request: Request) -> JSONResponse:
    """Host/v1 status envelope (parity with `product-factory host status`)."""
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.status(run_id))


@router.get("/runs/{run_id}/inspect")
def run_inspect(run_id: str, request: Request) -> JSONResponse:
    """Host/v1 inspect envelope (parity with `product-factory host inspect`)."""
    host = _state(request).host(observe_base_url=_observe_base(request))
    return _host_json(host.inspect(run_id))


@router.get("/runs/{run_id}/tail")
def run_tail(
    run_id: str,
    request: Request,
    after_seq: int = Query(0, ge=0),
) -> JSONResponse:
    """One host/v1 event batch (parity with `product-factory host tail --once`)."""
    host = _state(request).host(observe_base_url=_observe_base(request))
    batch = next(
        host.tail(
            run_id,
            after_seq=after_seq,
            follow=False,
            max_idle_polls=1,
            stop_when_terminal=False,
        ),
        None,
    )
    if batch is None:
        return _host_json(
            HostResponse.failure(code="not_found", message=f"Unknown run {run_id}", run_id=run_id)
        )
    return _host_json(batch)


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, request: Request, body: ApproveBody = ApproveBody()) -> JSONResponse:
    if remote_mode_enabled() and body.apply:
        return _host_json(
            HostResponse.failure(
                code="remote_apply_rejected",
                message=(
                    "Remote approval never applies changes on the server. "
                    "Approve with apply=false, then fetch and land the delivery locally."
                ),
                run_id=run_id,
            )
        )
    host = _state(request).host(observe_base_url=_observe_base(request))
    response = host.approve(run_id, apply=body.apply)
    if response.ok and remote_mode_enabled():
        row = _state(request).db.get_run(run_id)
        if row is not None:
            try:
                delivery = DeliveryStore(_state(request).data_dir).build(run_id, row)
            except DeliveryError as exc:
                return _host_json(
                    HostResponse.failure(
                        code="delivery_build_failed",
                        message=str(exc),
                        run_id=run_id,
                    )
                )
            response.data = {**(response.data or {}), "delivery": delivery.model_dump(mode="json")}
    return _host_json(response)


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
