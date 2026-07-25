"""Contract tests for host control HTTP API (P3.B), parallel to CLI host tests."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402
from product_factory.host.protocol import HOST_PROTOCOL, HostResponse  # noqa: E402
from tests.conftest import clone_fixture  # noqa: E402


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    real_config = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(real_config, root / "config")
    return root


def _fixture_repo(tmp_path: Path) -> Path:
    real_root = Path(__file__).resolve().parents[2]
    return clone_fixture(real_root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


@pytest.fixture
def control_env(tmp_path: Path):
    project = _project_root(tmp_path)
    fixture = _fixture_repo(tmp_path)
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "runs").mkdir(parents=True)
    app = create_app(data_dir, project_root=project)
    with TestClient(app) as client:
        yield client, fixture, data_dir


def _wait_for_status(
    client: TestClient,
    run_id: str,
    *,
    wanted: set[str],
    timeout: float = 60.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v1/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") in wanted:
                return last
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {wanted}; last={last}")


def test_control_submit_status_approve(control_env) -> None:
    client, fixture, _ = control_env
    submitted = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Add a validated health-check endpoint with tests.",
            "repository_path": str(fixture),
            "workflow_type": "code_change",
            "mock": True,
            "sync": True,
        },
    )
    assert submitted.status_code == 202, submitted.text
    body = HostResponse.model_validate(submitted.json())
    assert body.ok
    assert body.protocol == HOST_PROTOCOL
    assert body.run_id
    assert body.status == "queued"
    assert body.subscription is not None
    assert body.subscription.sse_url is not None
    assert f"/api/v1/runs/{body.run_id}/events/stream" in body.subscription.sse_url

    terminal = _wait_for_status(
        client,
        body.run_id,
        wanted={"awaiting_approval", "completed", "failed", "blocked", "budget_exhausted"},
    )
    assert terminal["status"] in {"awaiting_approval", "completed"}

    if terminal["status"] == "awaiting_approval":
        approved = client.post(f"/api/v1/runs/{body.run_id}/approve", json={})
        assert approved.status_code == 200, approved.text
        payload = HostResponse.model_validate(approved.json())
        assert payload.ok
        assert payload.status == "completed"
        assert payload.data is not None
        assert payload.data["approval"]["status"] == "approved"


def test_control_reject_round_trip(control_env) -> None:
    client, fixture, _ = control_env
    submitted = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Add a validated health-check endpoint with tests.",
            "repository_path": str(fixture),
            "mock": True,
            "sync": True,
        },
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    status = _wait_for_status(
        client, run_id, wanted={"awaiting_approval", "completed", "failed", "blocked"}
    )
    assert status["status"] == "awaiting_approval"

    rejected = client.post(f"/api/v1/runs/{run_id}/reject")
    assert rejected.status_code == 200, rejected.text
    payload = HostResponse.model_validate(rejected.json())
    assert payload.ok
    assert payload.status == "blocked"
    assert payload.data is not None
    assert payload.data["approval"]["status"] == "rejected"


def test_control_plan_preview(control_env) -> None:
    client, _, _ = control_env
    resp = client.post(
        "/api/v1/plan",
        json={
            "request_text": "Investigate health-check coverage and propose a plan.",
            "workflow_type": "code_change",
            "mock": True,
        },
    )
    assert resp.status_code == 200, resp.text
    payload = HostResponse.model_validate(resp.json())
    assert payload.ok
    assert payload.protocol == HOST_PROTOCOL
    assert payload.plan_summary is not None
    assert payload.plan_summary.get("task_count", 0) >= 1
    assert payload.data is not None
    assert payload.data["compiler"]["ok"] is True
    assert client.get("/api/v1/runs").json() == []


def test_control_cancel_via_host_service(control_env) -> None:
    """Cancel goes through HostService (live when P3.E is present)."""
    client, fixture, _ = control_env
    submitted = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Add a validated health-check endpoint with tests.",
            "repository_path": str(fixture),
            "mock": True,
            "sync": True,
        },
    )
    run_id = submitted.json()["run_id"]
    _wait_for_status(
        client, run_id, wanted={"awaiting_approval", "completed", "failed", "blocked"}
    )

    cancel = client.post(f"/api/v1/runs/{run_id}/cancel")
    cancel_body = HostResponse.model_validate(cancel.json())
    if cancel_body.error and cancel_body.error.code == "not_implemented":
        assert cancel.status_code == 501
    else:
        assert cancel.status_code == 200, cancel.text
        assert cancel_body.ok
        assert cancel_body.status == "cancelled"

    # Revise is wired the same way; assert route exists without re-running orchestration.
    revise = client.post("/api/v1/runs/missing-run/revise", json={"note": "nudge"})
    revise_body = HostResponse.model_validate(revise.json())
    assert revise_body.protocol == HOST_PROTOCOL
    if revise_body.error and revise_body.error.code == "not_implemented":
        assert revise.status_code == 501
    else:
        # Unknown run or invalid state → HostResponse failure (not 404 HTML).
        assert revise.status_code in {400, 404}
        assert revise_body.ok is False


def test_control_writes_require_token_when_configured(control_env, monkeypatch) -> None:
    client, fixture, _ = control_env
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "secret-token")
    denied = client.post(
        "/api/v1/runs",
        json={
            "request_text": "x",
            "repository_path": str(fixture),
            "mock": True,
            "sync": True,
        },
    )
    assert denied.status_code == 401

    # Reads remain open on loopback even when token is configured.
    assert client.get("/api/v1/health").status_code == 200

    allowed = client.post(
        "/api/v1/plan",
        headers={"Authorization": "Bearer secret-token"},
        json={"request_text": "Plan a health check", "mock": True},
    )
    assert allowed.status_code == 200, allowed.text
    assert HostResponse.model_validate(allowed.json()).ok


def test_control_openapi_lists_write_routes(control_env) -> None:
    client, _, _ = control_env
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/runs" in paths
    assert "post" in paths["/api/v1/runs"]
    assert "/api/v1/plan" in paths
    assert "/api/v1/runs/{run_id}/approve" in paths
    assert "/api/v1/runs/{run_id}/reject" in paths
    assert "/api/v1/runs/{run_id}/cancel" in paths
    assert "/api/v1/runs/{run_id}/revise" in paths
    assert "/api/v1/runs/{run_id}/materialize" in paths
    assert "/api/v1/runs/{run_id}/materialize-all" in paths


def test_control_materialize_happy_path_and_path_escape(control_env) -> None:
    client, fixture, data_dir = control_env
    host = client.app.state.api_state.host(mock=True)
    run_id = "run-http-mat"
    run_dir = data_dir / "runs" / run_id
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "output" / "ARCHITECTURE.md").write_text(
        "# ARCHITECTURE.md\n\n## Objective\nHTTP land.\n", encoding="utf-8"
    )
    request = {
        "request_id": "req-http-mat",
        "workflow_type": "technical_plan",
        "request_text": "Design it",
        "repository_path": str(fixture.resolve()),
        "model_profile_set": "local-target",
        "validation_commands": [],
        "budget": {"max_cost_usd": "3.00"},
        "metadata": {},
    }
    (run_dir / "input" / "request.json").write_text(
        json.dumps(request, indent=2) + "\n", encoding="utf-8"
    )
    host.coord.db.upsert_run(
        run_id=run_id,
        workflow_type="technical_plan",
        status="awaiting_approval",
        request=request,
    )

    ok = client.post(
        f"/api/v1/runs/{run_id}/materialize",
        json={
            "artifact": "ARCHITECTURE.md",
            "dest_path": "docs/ARCHITECTURE.md",
        },
    )
    assert ok.status_code == 200, ok.text
    body = HostResponse.model_validate(ok.json())
    assert body.ok
    assert body.data is not None
    assert (fixture / "docs" / "ARCHITECTURE.md").is_file()

    denied = client.post(
        f"/api/v1/runs/{run_id}/materialize",
        json={
            "artifact": "ARCHITECTURE.md",
            "dest_path": "../escape.md",
            "overwrite": True,
        },
    )
    assert denied.status_code == 400
    err = HostResponse.model_validate(denied.json())
    assert err.ok is False
    assert err.error is not None
    assert err.error.code == "path_escape"


def test_control_materialize_all_lands_named_deliverable(control_env) -> None:
    client, fixture, data_dir = control_env
    host = client.app.state.api_state.host(mock=True)
    run_id = "run-http-mat-all"
    run_dir = data_dir / "runs" / run_id
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "output" / "integration_testing_architecture.md").write_text(
        "# integration_testing_architecture.md\n\n## Objective\nScoped.\n",
        encoding="utf-8",
    )
    request = {
        "request_id": "req-http-mat-all",
        "workflow_type": "technical_plan",
        "request_text": "Design integration testing",
        "repository_path": str(fixture.resolve()),
        "model_profile_set": "local-target",
        "validation_commands": [],
        "artifact_overrides": {
            "architecture_document": {
                "dest_path": "docs/integration_testing_architecture.md"
            }
        },
        "budget": {"max_cost_usd": "3.00"},
        "metadata": {},
    }
    (run_dir / "input" / "request.json").write_text(
        json.dumps(request, indent=2) + "\n", encoding="utf-8"
    )
    host.coord.db.upsert_run(
        run_id=run_id,
        workflow_type="technical_plan",
        status="completed",
        request=request,
    )

    response = client.post(f"/api/v1/runs/{run_id}/materialize-all", json={})
    assert response.status_code == 200, response.text
    body = HostResponse.model_validate(response.json())
    assert body.ok
    assert body.data is not None
    assert [entry["role"] for entry in body.data["landed"]] == ["architecture_document"]
    assert (fixture / "docs" / "integration_testing_architecture.md").is_file()


def test_control_submit_rejects_unsafe_artifact_override(control_env) -> None:
    client, _, _ = control_env
    response = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Design it",
            "workflow_type": "technical_plan",
            "artifact_overrides": {
                "architecture_document": {"dest_path": "../../escape.md"}
            },
            "mock": True,
            "sync": True,
        },
    )
    assert response.status_code == 400, response.text
    body = HostResponse.model_validate(response.json())
    assert body.ok is False
    assert body.error is not None
    assert body.error.code == "invalid_artifact_override"
