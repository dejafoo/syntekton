"""FastAPI application factory for the observability API."""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from product_factory.api.control import router as control_router
from product_factory.api.deps import ApiState
from product_factory.api.routes import router
from product_factory.api.streaming import HEARTBEAT_SECONDS, MAX_QUEUE, iter_events


def create_app(
    data_dir: Path,
    *,
    cors_origins: list[str] | None = None,
    project_root: Path | None = None,
    observe_base_url: str | None = None,
) -> FastAPI:
    state = ApiState(
        data_dir.resolve(),
        project_root=project_root,
        observe_base_url=observe_base_url,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.api_state = state
        yield
        state.close()

    app = FastAPI(
        title="Product Factory Observability API",
        version="1.0.0",
        description=(
            "Local observability (read) and host control (write) API for "
            "orchestration events and run lifecycle."
        ),
        lifespan=lifespan,
    )
    # Available immediately for TestClient and startup races.
    app.state.api_state = state
    origins = cors_origins or []
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)
    app.include_router(control_router)
    dashboard_dir = Path(__file__).with_name("static") / "dashboard"
    if dashboard_dir.is_dir():
        # Deliberately mounted after /api/v1 and docs: this is a single-user
        # local UI, not a second backend or a mutation surface.
        app.mount(
            "/dashboard/assets",
            StaticFiles(directory=dashboard_dir / "assets"),
            name="dashboard-assets",
        )

        @app.get("/dashboard/{path:path}", include_in_schema=False)
        async def dashboard(path: str = "") -> FileResponse:
            # SPA fallback is intentionally constrained to the packaged index.
            return FileResponse(dashboard_dir / "index.html")

    @app.websocket("/api/v1/events/ws")
    async def events_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        query = state.query
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            sub = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json({"type": "error", "summary": f"Invalid subscription: {exc}"})
            await websocket.close(code=1003)
            return

        run_ids = sub.get("run_ids") or []
        after_seq = int(sub.get("after_seq") or 0)
        types = sub.get("types")
        run_id = run_ids[0] if len(run_ids) == 1 else None
        if len(run_ids) > 1:
            # Multi-run: stream globally and filter client-side by membership.
            run_id = None

        await websocket.send_json(
            {
                "type": "subscribed",
                "after_seq": after_seq,
                "run_ids": run_ids,
                "types": types,
            }
        )

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=MAX_QUEUE)
        stop = asyncio.Event()

        async def producer() -> None:
            try:
                async for event in iter_events(
                    query,
                    run_id=run_id,
                    after_seq=after_seq,
                    types=types,
                    live=True,
                ):
                    if stop.is_set():
                        break
                    if run_ids and event.get("type") != "heartbeat":
                        rid = event.get("run_id")
                        if rid and rid not in run_ids:
                            continue
                    try:
                        queue.put_nowait({"type": "event", "event": event})
                    except asyncio.QueueFull:
                        # Slow consumer: signal resumable disconnect.
                        with contextlib.suppress(asyncio.QueueFull):
                            queue.put_nowait(
                                {
                                    "type": "error",
                                    "code": "slow_consumer",
                                    "summary": "Client too slow; reconnect with after_seq",
                                    "after_seq": event.get("seq", after_seq),
                                }
                            )
                        stop.set()
                        break
            finally:
                await queue.put(None)

        task = asyncio.create_task(producer())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"type": "heartbeat", "after_seq": after_seq})
                    continue
                if item is None:
                    break
                await websocket.send_json(item)
                if item.get("type") == "error" and item.get("code") == "slow_consumer":
                    await websocket.close(code=1013)
                    break
                ev = item.get("event") or {}
                if ev.get("seq") is not None:
                    after_seq = int(ev["seq"])
        except WebSocketDisconnect:
            pass
        finally:
            stop.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return app


def serve(
    data_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    cors_origins: list[str] | None = None,
) -> None:
    import os

    import uvicorn

    from product_factory.api.remote_mode import configured_control_token, resolve_project_root

    if host not in {"127.0.0.1", "::1", "localhost"}:
        if not configured_control_token():
            raise SystemExit(
                "Non-loopback host requires PRODUCT_FACTORY_OBSERVE_TOKEN "
                "(or PRODUCT_FACTORY_HOST_TOKEN) in the environment"
            )

    project_root = None
    if (os.environ.get("PRODUCT_FACTORY_ROOT") or "").strip():
        project_root = resolve_project_root(data_dir=data_dir)
    observe_base = (os.environ.get("PRODUCT_FACTORY_OBSERVE_URL") or "").strip() or None
    app = create_app(
        data_dir,
        cors_origins=cors_origins,
        project_root=project_root,
        observe_base_url=observe_base,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
