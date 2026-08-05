"""Security tests for bounded remote git-bundle uploads (PM5.E)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402
from product_factory.api.ingress import INGRESS_LIMITER  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    INGRESS_LIMITER.reset()


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", project / "config")
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "upload-token")
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_URL", "http://pf.test")
    app = create_app(data_dir, project_root=project, observe_base_url="http://pf.test")
    return TestClient(app), data_dir


def _git_bundle(tmp_path: Path) -> bytes:
    repo = tmp_path / "bundle-src"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("bundle\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    bundle = tmp_path / "repo.bundle"
    subprocess.run(
        ["git", "bundle", "create", str(bundle), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return bundle.read_bytes()


def test_upload_happy_path_verifies_hash_and_finalizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, data_dir = _app(tmp_path, monkeypatch)
    payload = _git_bundle(tmp_path)
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer upload-token"}
    with client:
        bounds = client.get("/api/v1/uploads/bounds", headers=headers)
        assert bounds.status_code == 200
        pre = client.post(
            "/api/v1/uploads/git-bundle/preflight",
            headers=headers,
            json={
                "declared_size": len(payload),
                "declared_sha256": digest,
                "media_type": "application/x-git-bundle",
                "filename": "repo.bundle",
            },
        )
        assert pre.status_code == 200, pre.text
        upload_id = pre.json()["upload_id"]
        put = client.put(
            f"/api/v1/uploads/git-bundle/{upload_id}",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=payload,
        )
        assert put.status_code == 200, put.text
        fin = client.post(
            f"/api/v1/uploads/git-bundle/{upload_id}/finalize",
            headers=headers,
        )
        assert fin.status_code == 200, fin.text
        body = fin.json()
        assert body["sha256"] == digest
        assert body["status"] == "finalized"
        assert (data_dir / "uploads" / "final" / f"{digest}.bundle").is_file()
        audit = (data_dir / "ops" / "ingress-audit.jsonl").read_text(encoding="utf-8")
        assert "ingress.upload_finalized" in audit


def test_upload_rejects_digest_mismatch_and_hostile_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _app(tmp_path, monkeypatch)
    payload = _git_bundle(tmp_path)
    headers = {"Authorization": "Bearer upload-token"}
    with client:
        pre = client.post(
            "/api/v1/uploads/git-bundle/preflight",
            headers=headers,
            json={
                "declared_size": len(payload),
                "declared_sha256": "0" * 64,
                "media_type": "application/x-git-bundle",
            },
        )
        upload_id = pre.json()["upload_id"]
        bad = client.put(
            f"/api/v1/uploads/git-bundle/{upload_id}",
            headers=headers,
            content=payload,
        )
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "upload_rejected"

        hostile = b"# v2 git bundle\n../etc/passwd\n\n" + b"x" * 32
        digest = hashlib.sha256(hostile).hexdigest()
        pre2 = client.post(
            "/api/v1/uploads/git-bundle/preflight",
            headers=headers,
            json={
                "declared_size": len(hostile),
                "declared_sha256": digest,
                "media_type": "application/x-git-bundle",
            },
        )
        upload_id2 = pre2.json()["upload_id"]
        denied = client.put(
            f"/api/v1/uploads/git-bundle/{upload_id2}",
            headers=headers,
            content=hostile,
        )
        assert denied.status_code == 400
        assert "path-escape" in denied.json()["error"]["message"]

        oversize = client.post(
            "/api/v1/uploads/git-bundle/preflight",
            headers=headers,
            json={
                "declared_size": 10**12,
                "declared_sha256": "a" * 64,
                "media_type": "application/x-git-bundle",
            },
        )
        assert oversize.status_code == 400

        bad_type = client.post(
            "/api/v1/uploads/git-bundle/preflight",
            headers=headers,
            json={
                "declared_size": 10,
                "declared_sha256": "b" * 64,
                "media_type": "application/zip",
            },
        )
        assert bad_type.status_code == 400
