"""FastAPI application factory for the observability API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from product_factory.api.control import router as control_router
from product_factory.api.control_v2 import router as control_v2_router
from product_factory.api.delivery import router as delivery_router
from product_factory.api.deps import ApiState
from product_factory.api.routes import router
from product_factory.api.uploads import router as uploads_router


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
        if os.environ.get("PRODUCT_FACTORY_REMOTE_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            # Construct the service at process startup so its durable lease
            # scanner can recover interrupted remote workers before a client
            # happens to issue another control request.
            state.host(observe_base_url=observe_base_url)
        yield
        state.close()

    app = FastAPI(
        title="Product Factory Observability API",
        version="1.0.0",
        description=(
            "Local observability (read) and host control (write) API for "
            "orchestration events and run lifecycle. Live events use cursor-resumable SSE."
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
    app.include_router(control_v2_router)
    app.include_router(delivery_router)
    app.include_router(uploads_router)
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
