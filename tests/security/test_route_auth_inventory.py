"""SD0.D — remote route authentication inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import WebSocketRoute  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402
from product_factory.persistence.database import Database  # noqa: E402


def _app(tmp_path: Path):
    data_dir = tmp_path / ".product-factory"
    Database(data_dir / "data" / "product_factory.sqlite").close()
    return create_app(data_dir)


def test_no_websocket_live_stream_route(tmp_path: Path) -> None:
    app = _app(tmp_path)
    ws_paths = [r.path for r in app.routes if isinstance(r, WebSocketRoute)]
    assert "/api/v1/events/ws" not in ws_paths
    assert not any(path.endswith("/events/ws") for path in ws_paths)


def test_remote_mode_requires_auth_on_api_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "test-token-sd0d")
    # Token-required auth must hold without starting the remote worker scanner.
    monkeypatch.delenv("PRODUCT_FACTORY_REMOTE_MODE", raising=False)
    app = _app(tmp_path)
    with TestClient(app) as client:
        # Observe
        assert client.get("/api/v1/health").status_code == 401
        assert client.get("/api/v1/runs").status_code == 401
        assert client.get("/api/v1/events").status_code == 401
        # SSE
        with client.stream("GET", "/api/v1/runs/missing/events/stream") as resp:
            assert resp.status_code == 401
        # Control
        assert client.post("/api/v1/runs", json={}).status_code == 401
        # Authed health works
        ok = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer test-token-sd0d"},
        )
        assert ok.status_code == 200

    # Ensure inventory: every /api/v1 HTTP route carries an auth dependency.
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/api/v1"):
            continue
        if isinstance(route, WebSocketRoute):
            pytest.fail(f"Unexpected WebSocket API route: {path}")
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        dep_names = {getattr(d.call, "__name__", "") for d in dependant.dependencies}
        assert "require_auth" in dep_names or "require_write_auth" in dep_names, path
