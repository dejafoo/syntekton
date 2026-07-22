"""REST routes for observability API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from product_factory.api.auth import require_auth
from product_factory.api.deps import ApiState
from product_factory.api.streaming import encode_sse, iter_events
from product_factory.observability.contracts import (
    ArtifactView,
    HealthView,
    ModelInvocationView,
    PromptPackageView,
    RunSummary,
    TaskSummary,
    ToolCallView,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_auth)])


def _state(request: Request) -> ApiState:
    return request.app.state.api_state


@router.get("/health", response_model=HealthView)
def health(request: Request) -> HealthView:
    return _state(request).query.health()


@router.get("/meta")
def meta(request: Request) -> dict:
    h = _state(request).query.health()
    return {
        "api_version": "v1",
        "schema_version": 1,
        "latest_seq": h.latest_seq,
        "capture_level": h.capture_level,
        "wal_mode": h.wal_mode,
    }


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    status: str | None = None,
) -> list[RunSummary]:
    return _state(request).query.list_runs(limit=limit, status=status)


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(run_id: str, request: Request) -> RunSummary:
    run = _state(request).query.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/tasks", response_model=list[TaskSummary])
def list_tasks(run_id: str, request: Request) -> list[TaskSummary]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_tasks(run_id)


@router.get("/runs/{run_id}/tasks/{task_id}", response_model=TaskSummary)
def get_task(run_id: str, task_id: str, request: Request) -> TaskSummary:
    task = _state(request).query.get_task(run_id, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/runs/{run_id}/events")
def list_events(
    run_id: str,
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    types: str | None = None,
) -> dict:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    type_list = [t.strip() for t in types.split(",")] if types else None
    items = _state(request).query.list_events(
        run_id=run_id, after_seq=after_seq, limit=limit, types=type_list
    )
    next_cursor = items[-1]["seq"] if items else after_seq
    return {"items": items, "next_cursor": next_cursor, "has_more": len(items) >= limit}


@router.get("/events")
def list_global_events(
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    run_id: str | None = None,
    types: str | None = None,
) -> dict:
    type_list = [t.strip() for t in types.split(",")] if types else None
    items = _state(request).query.list_events(
        run_id=run_id, after_seq=after_seq, limit=limit, types=type_list
    )
    next_cursor = items[-1]["seq"] if items else after_seq
    return {"items": items, "next_cursor": next_cursor, "has_more": len(items) >= limit}


@router.get("/runs/{run_id}/model-invocations", response_model=list[ModelInvocationView])
def list_invocations(run_id: str, request: Request) -> list[ModelInvocationView]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_invocations(run_id)


@router.get("/runs/{run_id}/tool-calls", response_model=list[ToolCallView])
def list_tool_calls(run_id: str, request: Request) -> list[ToolCallView]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_tool_calls(run_id)


@router.get("/runs/{run_id}/validations")
def list_validations(run_id: str, request: Request) -> list[dict]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_validations(run_id)


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactView])
def list_artifacts(run_id: str, request: Request) -> list[ArtifactView]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_artifacts_for_run(run_id)


@router.get("/runs/{run_id}/prompts", response_model=list[PromptPackageView])
def list_prompts(run_id: str, request: Request) -> list[PromptPackageView]:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _state(request).query.list_prompts(run_id)


@router.get("/runs/{run_id}/events/stream")
async def stream_events_sse(
    run_id: str,
    request: Request,
    after_seq: Annotated[int, Query(ge=0)] = 0,
    types: str | None = None,
    live: Annotated[bool, Query()] = True,
) -> StreamingResponse:
    if _state(request).query.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    type_list = [t.strip() for t in types.split(",")] if types else None
    query = _state(request).query

    async def gen():
        async for event in iter_events(
            query, run_id=run_id, after_seq=after_seq, types=type_list, live=live
        ):
            if await request.is_disconnected():
                break
            yield encode_sse(event)

    return StreamingResponse(gen(), media_type="text/event-stream")
