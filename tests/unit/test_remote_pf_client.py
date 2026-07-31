"""Unit/contract tests for PM2.B remote server readiness + RemotePfClient."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402
from product_factory.config.repositories import load_repositories_config  # noqa: E402
from product_factory.host.protocol import HOST_PROTOCOL, HostResponse  # noqa: E402
from product_factory.remote.client import (  # noqa: E402
    PfProtocolError,
    PfRemoteError,
    RemotePfClient,
    assert_protocol,
)
from product_factory.remote.sse import _parse_sse_chunk  # noqa: E402
from tests.conftest import clone_fixture  # noqa: E402


def _sync_asgi_client(app) -> httpx.Client:
    """Bridge FastAPI app to sync httpx.Client (ASGITransport is async-only)."""
    starlette = TestClient(app)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        response = starlette.request(
            request.method,
            path,
            headers={k: v for k, v in request.headers.items()},
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    return httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))


def _project_root(tmp_path: Path, *, repos: dict[str, str] | None = None) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    real_config = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(real_config, root / "config")
    if repos is not None:
        lines = ["repositories:\n"]
        for repo_id, path in repos.items():
            lines.append(f"  {repo_id}:\n")
            lines.append(f"    path: {path}\n")
            lines.append(f"    description: test repo {repo_id}\n")
        (root / "config" / "repositories.yaml").write_text("".join(lines), encoding="utf-8")
    return root


def _fixture_repo(tmp_path: Path) -> Path:
    real_root = Path(__file__).resolve().parents[2]
    return clone_fixture(real_root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


@pytest.fixture
def remote_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _fixture_repo(tmp_path)
    project = _project_root(tmp_path, repos={"sample_api": str(fixture.resolve())})
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "runs").mkdir(parents=True)
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_URL", "http://pf.test")
    monkeypatch.delenv("PRODUCT_FACTORY_REMOTE_MODE", raising=False)
    monkeypatch.delenv("PRODUCT_FACTORY_OBSERVE_TOKEN", raising=False)
    monkeypatch.delenv("PRODUCT_FACTORY_HOST_TOKEN", raising=False)
    app = create_app(data_dir, project_root=project, observe_base_url="http://pf.test")
    with TestClient(app) as client:
        yield client, fixture, data_dir, project


def test_meta_advertises_remote_capabilities(remote_env) -> None:
    client, _, _, _ = remote_env
    meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    body = meta.json()
    assert body["protocol"] == HOST_PROTOCOL
    assert body["api_version"] == "v1"
    assert body["remote_mode"] is False
    assert body["supported_workspace_kinds"] == ["none", "registered_path", "git_ref"]
    assert body["delivery_support"] is False
    assert body["repository_ids"] == ["sample_api"]
    assert body["canonical_observe_base"] == "http://pf.test"


def test_observe_requires_bearer_when_token_configured(remote_env, monkeypatch) -> None:
    client, _, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "secret")
    assert client.get("/api/v1/meta").status_code == 401
    assert client.get("/api/v1/health").status_code == 401
    ok = client.get("/api/v1/meta", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    # HOST_TOKEN alias is accepted by the server.
    monkeypatch.delenv("PRODUCT_FACTORY_OBSERVE_TOKEN")
    monkeypatch.setenv("PRODUCT_FACTORY_HOST_TOKEN", "alias-secret")
    denied = client.get("/api/v1/meta")
    assert denied.status_code == 401
    aliased = client.get("/api/v1/meta", headers={"Authorization": "Bearer alias-secret"})
    assert aliased.status_code == 200


def test_remote_mode_rejects_laptop_repository_path(remote_env, monkeypatch) -> None:
    client, fixture, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    denied = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Plan a health check",
            "repository_path": str(fixture),
            "workflow_type": "technical_plan",
            "mock": True,
            "sync": True,
        },
    )
    assert denied.status_code == 400
    body = HostResponse.model_validate(denied.json())
    assert body.ok is False
    assert body.error is not None
    assert body.error.code == "remote_repository_path_rejected"


def test_remote_approve_never_maps_apply_to_server_workspace(remote_env, monkeypatch) -> None:
    client, _, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    denied = client.post("/api/v1/runs/run-any/approve", json={"apply": True})
    assert denied.status_code == 400
    body = HostResponse.model_validate(denied.json())
    assert body.ok is False
    assert body.error is not None
    assert body.error.code == "remote_apply_rejected"


def test_remote_mode_accepts_repository_id(remote_env, monkeypatch) -> None:
    client, _, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    submitted = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Investigate health-check coverage and propose a plan.",
            "repository_id": "sample_api",
            "workflow_type": "technical_plan",
            "mock": True,
            "sync": True,
        },
    )
    assert submitted.status_code == 202, submitted.text
    body = HostResponse.model_validate(submitted.json())
    assert body.ok
    assert body.protocol == HOST_PROTOCOL
    assert body.run_id
    assert body.subscription is not None
    assert body.subscription.sse_url is not None
    assert body.subscription.sse_url.startswith("http://pf.test/")


def test_remote_mode_git_ref_records_exact_provenance(remote_env, monkeypatch) -> None:
    client, fixture, _, project = remote_env
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=fixture,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (project / "config" / "repositories.yaml").write_text(
        "repositories:\n"
        "  sample_api:\n"
        f"    path: {fixture}\n"
        f"    fetch_url: {fixture}\n"
        "    refs:\n"
        "      - refs/heads/*\n",
        encoding="utf-8",
    )
    branch = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=fixture,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")

    submitted = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Investigate health-check coverage and propose a plan.",
            "workflow_type": "technical_plan",
            "workspace": {
                "kind": "git_ref",
                "repository_id": "sample_api",
                "ref": f"refs/heads/{branch}",
                "commit": commit,
            },
            "mock": True,
            "sync": True,
        },
    )
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["run_id"]
    inspected = client.get(f"/api/v1/runs/{run_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    provenance = inspected.json()["data"]["workspace_provenance"]
    assert provenance == {
        "kind": "git_ref",
        "repository_id": "sample_api",
        "ref": f"refs/heads/{branch}",
        "commit": commit,
    }
    assert inspected.json()["data"]["manifest"]["base_commit"] == commit
    assert inspected.json()["data"]["manifest"]["workspace_provenance"] == provenance


def test_remote_mode_rejects_unpinned_default_git_ref(remote_env, monkeypatch) -> None:
    client, fixture, _, project = remote_env
    (project / "config" / "repositories.yaml").write_text(
        "repositories:\n"
        "  sample_api:\n"
        f"    fetch_url: {fixture}\n"
        "    refs:\n"
        "      - refs/heads/*\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    denied = client.post(
        "/api/v1/runs",
        json={
            "request_text": "Plan a health check",
            "workspace": {
                "kind": "git_ref",
                "repository_id": "sample_api",
                "ref": "HEAD",
            },
            "mock": True,
            "sync": True,
        },
    )
    assert denied.status_code == 400
    error = HostResponse.model_validate(denied.json()).error
    assert error is not None
    assert error.code == "invalid_workspace"


def test_repositories_loader_resolves_absolute_paths(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = (tmp_path / "repos" / "demo").resolve()
    target.mkdir(parents=True)
    (config_dir / "repositories.yaml").write_text(
        f"repositories:\n  demo:\n    path: {target}\n    description: demo\n",
        encoding="utf-8",
    )
    cfg = load_repositories_config(config_dir)
    assert cfg.ids() == ["demo"]
    assert cfg.resolve("demo") == target


def test_remote_client_host_v1_parity(remote_env, monkeypatch) -> None:
    client, _, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    with RemotePfClient(base_url="http://test", client=_sync_asgi_client(client.app)) as remote:
        meta = remote.meta()
        assert meta["protocol"] == HOST_PROTOCOL
        assert "sample_api" in meta["repository_ids"]

        submitted = remote.submit(
            request_text="Investigate health-check coverage and propose a plan.",
            workflow_type="technical_plan",
            repository_id="sample_api",
            mock=True,
            sync=True,
        )
        assert submitted.ok
        assert submitted.run_id
        status = remote.status(submitted.run_id)
        assert status.ok
        assert status.protocol == HOST_PROTOCOL
        inspected = remote.inspect(submitted.run_id)
        assert inspected.ok
        assert inspected.data is not None
        tailed = remote.tail(submitted.run_id, after_seq=0)
        assert tailed.ok
        assert isinstance(tailed.events, list)



def test_remote_client_rejects_repository_path_locally() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("submit must reject repository_path before HTTP")

    with (
        RemotePfClient(base_url="http://test", transport=httpx.MockTransport(handler)) as remote,
        pytest.raises(PfRemoteError, match="rejects repository_path"),
    ):
        remote.submit(request_text="x", repository_path="/Users/me/project")


def test_remote_client_protocol_mismatch_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "protocol": "product-factory.host/v0",
                "ok": True,
                "run_id": "run-x",
                "status": "queued",
                "artifacts": [],
                "events": [],
            },
        )

    with (
        RemotePfClient(base_url="http://test", transport=httpx.MockTransport(handler)) as remote,
        pytest.raises(PfProtocolError, match="Unexpected host protocol"),
    ):
        remote.status("run-x")


def test_remote_client_missing_token_when_required(remote_env, monkeypatch) -> None:
    client, _, _, _ = remote_env
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "secret")
    with (
        RemotePfClient(
            base_url="http://test", token=None, client=_sync_asgi_client(client.app)
        ) as remote,
        pytest.raises(PfRemoteError, match="Unauthorized"),
    ):
        remote.token = None
        remote.meta()


def test_assert_protocol_helper() -> None:
    ok = assert_protocol(
        {
            "protocol": HOST_PROTOCOL,
            "ok": True,
            "artifacts": [],
            "events": [],
        }
    )
    assert ok.ok
    with pytest.raises(PfProtocolError):
        assert_protocol({"protocol": "other", "ok": True, "artifacts": [], "events": []})


def test_sse_chunk_parser_dedupes_by_seq() -> None:
    buffer = (
        'id: 1\nevent: run.progress\ndata: {"seq": 1, "type": "run.progress"}\n\n'
        'id: 1\nevent: run.progress\ndata: {"seq": 1, "type": "run.progress"}\n\n'
        'id: 2\ndata: {"hello": true}\n\n'
        "incomplete"
    )
    events, remainder = _parse_sse_chunk(buffer)
    assert remainder == "incomplete"
    assert len(events) == 3
    assert events[0]["seq"] == 1
    assert events[2]["seq"] == 2
    assert events[2]["hello"] is True


def test_host_status_inspect_routes_exist(remote_env) -> None:
    client, _, _, _ = remote_env
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/runs/{run_id}/status" in paths
    assert "/api/v1/runs/{run_id}/inspect" in paths
    assert "/api/v1/runs/{run_id}/tail" in paths
    missing = client.get("/api/v1/runs/missing/status")
    body = HostResponse.model_validate(missing.json())
    assert missing.status_code == 404
    assert body.ok is False
    assert body.error is not None
    assert body.error.code == "not_found"
