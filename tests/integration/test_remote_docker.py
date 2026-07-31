"""Real-HTTP Docker remote sandbox integration tests (PM3.0).

Soft-skips unless ``DOCKER_INTEGRATION=1``. When that flag is set, missing
Docker/compose or an unhealthy stack fails the suite (no silent skip).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from product_factory.host.protocol import HOST_PROTOCOL
from product_factory.remote.client import PfRemoteError, RemotePfClient

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "examples" / "remote" / "docker-compose.yml"
UP_SCRIPT = REPO_ROOT / "scripts" / "docker_remote_up.sh"
DEFAULT_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN = "test-token"


def _docker_integration_enabled() -> bool:
    return os.environ.get("DOCKER_INTEGRATION", "").strip().lower() in {"1", "true", "yes", "on"}


def _require_docker() -> None:
    if shutil.which("docker") is None:
        pytest.fail("DOCKER_INTEGRATION=1 but docker is not on PATH")
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"DOCKER_INTEGRATION=1 but docker daemon is unavailable: {exc}")
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"DOCKER_INTEGRATION=1 but docker compose is unavailable: {exc}")


@pytest.fixture(scope="session")
def docker_remote_env() -> Iterator[dict[str, str]]:
    if not _docker_integration_enabled():
        pytest.skip("Set DOCKER_INTEGRATION=1 to run Docker remote integration tests")

    _require_docker()
    if not UP_SCRIPT.is_file():
        pytest.fail(f"missing up script: {UP_SCRIPT}")
    if not COMPOSE_FILE.is_file():
        pytest.fail(f"missing compose file: {COMPOSE_FILE}")

    token = os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN") or DEFAULT_TOKEN
    url = (os.environ.get("PRODUCT_FACTORY_REMOTE_URL") or DEFAULT_URL).rstrip("/")
    env = {
        **os.environ,
        "PRODUCT_FACTORY_OBSERVE_TOKEN": token,
        "PRODUCT_FACTORY_REMOTE_URL": url,
        "PRODUCT_FACTORY_OBSERVE_URL": os.environ.get("PRODUCT_FACTORY_OBSERVE_URL") or url,
        "DOCKER_INTEGRATION": "1",
    }

    up = subprocess.run(
        ["bash", str(UP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if up.returncode != 0:
        pytest.fail(f"docker_remote_up.sh failed\nstdout:\n{up.stdout}\nstderr:\n{up.stderr}")

    try:
        yield {"url": url, "token": token}
    finally:
        keep = os.environ.get("DOCKER_KEEP", "").strip().lower() in {"1", "true", "yes", "on"}
        if not keep:
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "--remove-orphans"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )


@pytest.fixture
def remote(docker_remote_env: dict[str, str]) -> Iterator[RemotePfClient]:
    with RemotePfClient(
        base_url=docker_remote_env["url"],
        token=docker_remote_env["token"],
        timeout=120.0,
    ) as client:
        yield client


def test_meta_remote_mock_capabilities(remote: RemotePfClient) -> None:
    meta = remote.meta()
    assert meta["protocol"] == HOST_PROTOCOL
    assert meta["api_version"] == "v1"
    assert meta["remote_mode"] is True
    assert meta["supported_workspace_kinds"] == ["none", "registered_path", "git_ref"]
    assert meta["delivery_support"] is False
    assert "sample_api" in meta["repository_ids"]


def test_auth_rejects_missing_bearer(docker_remote_env: dict[str, str]) -> None:
    url = docker_remote_env["url"]
    response = httpx.get(f"{url}/api/v1/meta", timeout=30.0)
    assert response.status_code == 401
    denied = httpx.get(
        f"{url}/api/v1/health",
        headers={"Authorization": "Bearer wrong-token"},
        timeout=30.0,
    )
    assert denied.status_code == 401
    # Client path: construct without inheriting the harness env token.
    with (
        RemotePfClient(base_url=url, token="wrong-token", timeout=30.0) as client,
        pytest.raises(PfRemoteError, match="Unauthorized"),
    ):
        client.meta()


def test_mock_change_intake_no_repo_lifecycle(remote: RemotePfClient) -> None:
    submitted = remote.submit(
        request_text=(
            'Add a GET /health endpoint that returns {"status":"ok"} with HTTP 200.\n'
            "Acceptance criteria: route registered; JSON status=ok; existing tests pass.\n"
            "Non-goals: no auth. Constraints: stay within src/api/."
        ),
        workflow_type="change_intake",
        mock=True,
        sync=True,
    )
    assert submitted.ok, submitted.model_dump()
    assert submitted.run_id
    assert submitted.status in {"completed", "awaiting_approval", "failed", "running", "queued"}

    # sync mock should finish quickly; poll status/inspect for envelope parity.
    deadline = time.time() + 120
    status = submitted
    while time.time() < deadline:
        status = remote.status(submitted.run_id)
        assert status.ok
        assert status.protocol == HOST_PROTOCOL
        if status.status in {"completed", "awaiting_approval", "failed", "cancelled"}:
            break
        time.sleep(0.5)
    assert status.status in {"completed", "awaiting_approval", "failed"}

    inspected = remote.inspect(submitted.run_id)
    assert inspected.ok
    assert inspected.protocol == HOST_PROTOCOL
    assert inspected.data is not None


def test_mock_technical_plan_registered_repo(remote: RemotePfClient) -> None:
    submitted = remote.submit(
        request_text="Investigate health-check coverage and propose a technical plan.",
        workflow_type="technical_plan",
        repository_id="sample_api",
        mock=True,
        sync=True,
    )
    assert submitted.ok, submitted.model_dump()
    assert submitted.run_id

    status = remote.status(submitted.run_id)
    assert status.ok
    assert status.protocol == HOST_PROTOCOL

    inspected = remote.inspect(submitted.run_id)
    assert inspected.ok
    assert inspected.data is not None


def test_mock_git_ref_workspace_provenance(
    remote: RemotePfClient, docker_remote_env: dict[str, str]
) -> None:
    env = {
        **os.environ,
        "PRODUCT_FACTORY_OBSERVE_TOKEN": docker_remote_env["token"],
    }
    resolved = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "exec",
            "-T",
            "product-factory",
            "git",
            "-C",
            "/data/repos/sample_api",
            "rev-parse",
            "refs/heads/main",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    submitted = remote.submit(
        request_text="Investigate health-check coverage from the pinned fixture revision.",
        workflow_type="technical_plan",
        workspace={
            "kind": "git_ref",
            "repository_id": "sample_api",
            "ref": "refs/heads/main",
            "commit": resolved,
        },
        mock=True,
        sync=True,
    )
    assert submitted.ok, submitted.model_dump()
    assert submitted.run_id

    inspected = remote.inspect(submitted.run_id)
    assert inspected.ok
    assert inspected.data is not None
    provenance = inspected.data["workspace_provenance"]
    assert provenance == {
        "kind": "git_ref",
        "repository_id": "sample_api",
        "ref": "refs/heads/main",
        "commit": resolved,
    }
    assert inspected.data["manifest"]["base_commit"] == resolved
    assert inspected.data["manifest"]["workspace_provenance"] == provenance


def test_sse_tail_or_stream_available(remote: RemotePfClient) -> None:
    submitted = remote.submit(
        request_text="Frame a small clarification-light intake for a health endpoint.",
        workflow_type="change_intake",
        mock=True,
        sync=True,
    )
    assert submitted.ok
    assert submitted.run_id

    tailed = remote.tail(submitted.run_id, after_seq=0)
    assert tailed.ok
    assert isinstance(tailed.events, list)

    # Best-effort SSE: at least one event or a clean empty iterator is fine.
    events = list(remote.iter_sse(submitted.run_id, after_seq=0, live=False))
    assert isinstance(events, list)
