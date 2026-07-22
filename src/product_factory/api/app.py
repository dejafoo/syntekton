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

from product_factory.api.deps import ApiState
from product_factory.api.routes import router
from product_factory.api.streaming import HEARTBEAT_SECONDS, MAX_QUEUE, iter_events


def create_app(data_dir: Path, *, cors_origins: list[str] | None = None) -> FastAPI:
    state = ApiState(data_dir.resolve())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.api_state = state
        yield
        state.close()

    app = FastAPI(
        title="Product Factory Observability API",
        version="1.0.0",
        description="Read-only REST and streaming API for orchestration events.",
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
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type"],
        )
    app.include_router(router)

    @app.websocket("/api/v1/events/ws")
    async def events_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        query = state.query
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            sub = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            await websocket.send_json(
                {"type": "error", "summary": f"Invalid subscription: {exc}"}
            )
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
    import uvicorn

    if host not in {"127.0.0.1", "::1", "localhost"}:
        import os

        if not os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN"):
            raise SystemExit(
                "Non-loopback host requires PRODUCT_FACTORY_OBSERVE_TOKEN in the environment"
            )

    app = create_app(data_dir, cors_origins=cors_origins)
    uvicorn.run(app, host=host, port=port, log_level="info")
