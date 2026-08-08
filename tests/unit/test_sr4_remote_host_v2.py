"""SR4.B — remote Python client host/v2 negotiation and strict validation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from product_factory.api.app import create_app
from product_factory.host.protocol import HOST_PROTOCOL
from product_factory.host.protocol_v2 import HOST_PROTOCOL_V2, HostResponseV2
from product_factory.remote.client import (
    PfProtocolError,
    PfRemoteError,
    RemotePfClient,
    assert_protocol_v2,
)
from tests.conftest import clone_fixture


def _sync_asgi_client(app) -> httpx.Client:
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


@pytest.fixture
def remote_v2_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real_root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(real_root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    project = _project_root(tmp_path, repos={"sample_api": str(fixture.resolve())})
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "runs").mkdir(parents=True)
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_URL", "http://pf.test")
    monkeypatch.setenv("PRODUCT_FACTORY_FORCE_MOCK", "1")
    monkeypatch.setenv("PRODUCT_FACTORY_REMOTE_MODE", "true")
    monkeypatch.delenv("PRODUCT_FACTORY_OBSERVE_TOKEN", raising=False)
    monkeypatch.delenv("PRODUCT_FACTORY_HOST_TOKEN", raising=False)
    app = create_app(data_dir, project_root=project, observe_base_url="http://pf.test")
    with TestClient(app) as client:
        yield client


def test_assert_protocol_v2_rejects_unknown_fields() -> None:
    payload = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "fixtures"
            / "host_protocol"
            / "v2_submit_response.json"
        ).read_text(encoding="utf-8")
    )
    ok = assert_protocol_v2(payload)
    assert ok.protocol == HOST_PROTOCOL_V2
    assert ok.operation == "submit"

    forged = dict(payload)
    forged["secret_debug"] = True
    with pytest.raises(PfProtocolError, match="unknown fields"):
        assert_protocol_v2(forged)


def test_assert_protocol_v2_rejects_v1_envelope() -> None:
    with pytest.raises(PfProtocolError, match="Unexpected host protocol"):
        assert_protocol_v2(
            {
                "protocol": HOST_PROTOCOL,
                "ok": True,
                "artifacts": [],
                "events": [],
            }
        )


def test_remote_client_v2_meta_and_submit(remote_v2_env) -> None:
    client = remote_v2_env
    with RemotePfClient(
        base_url="http://test",
        client=_sync_asgi_client(client.app),
        protocol="v2",
    ) as remote:
        meta = remote.meta()
        assert meta["protocol"] == HOST_PROTOCOL_V2
        assert meta["default_protocol"] == HOST_PROTOCOL_V2
        assert HOST_PROTOCOL_V2 in meta["supported_protocols"]
        assert remote.active_protocol == HOST_PROTOCOL_V2

        submitted = remote.submit(
            request_text="Investigate health-check coverage and propose a plan.",
            workflow_type="technical_plan",
            repository_id="sample_api",
        )
        assert isinstance(submitted, HostResponseV2)
        assert submitted.ok
        assert submitted.protocol == HOST_PROTOCOL_V2
        assert submitted.operation == "submit"
        assert submitted.run_id

        status = remote.status(submitted.run_id)
        assert isinstance(status, HostResponseV2)
        assert status.ok
        assert status.protocol == HOST_PROTOCOL_V2


def test_remote_client_v2_rejects_mock_without_v1_fallback() -> None:
    """v2 compatibility-field rejection happens before any HTTP call."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"must not call HTTP after v2 field rejection: {request.url.path}")

    with (
        RemotePfClient(
            base_url="http://test",
            transport=httpx.MockTransport(handler),
            protocol="v2",
        ) as remote,
        pytest.raises(PfRemoteError, match="host/v2 rejects compatibility fields"),
    ):
        remote.submit(request_text="x", mock=True)


def test_remote_client_auto_prefers_v2(remote_v2_env) -> None:
    client = remote_v2_env
    with RemotePfClient(
        base_url="http://test",
        client=_sync_asgi_client(client.app),
        protocol="auto",
    ) as remote:
        assert remote.ensure_protocol() == HOST_PROTOCOL_V2
        meta = remote.meta()
        assert meta["protocol"] == HOST_PROTOCOL_V2


def test_remote_client_auto_selects_v1_when_server_lacks_v2() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/meta":
            return httpx.Response(404, request=request)
        if request.url.path == "/api/v1/meta":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "protocol": HOST_PROTOCOL,
                    "api_version": "v1",
                    "supported_protocols": [HOST_PROTOCOL],
                    "default_protocol": HOST_PROTOCOL,
                },
                request=request,
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    with RemotePfClient(
        base_url="http://test",
        transport=httpx.MockTransport(handler),
        protocol="auto",
    ) as remote:
        assert remote.ensure_protocol() == HOST_PROTOCOL


def test_golden_v2_response_fixture_strict() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "host_protocol"
        / "v2_submit_response.json"
    )
    body = assert_protocol_v2(json.loads(fixture.read_text(encoding="utf-8")))
    assert body.run_id == "run-example0001"
    assert body.result["workflow_type"] == "code_change"
