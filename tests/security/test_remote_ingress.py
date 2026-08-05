"""Security tests for remote ingress: proxy trust, auth limits, audit (PM5.E)."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402
from product_factory.api.ingress import (  # noqa: E402
    INGRESS_LIMITER,
    IngressConfig,
    load_ingress_config,
    resolve_client_ip,
)


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    INGRESS_LIMITER.reset()


def _scope(*, client: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers or [],
        "client": (client, 12345),
        "server": ("testserver", 80),
    }


def test_forwarded_headers_ignored_without_trusted_proxy() -> None:
    config = IngressConfig(trusted_proxies=[], trust_forwarded_headers=False)
    request = Request(
        _scope(
            client="10.0.0.1",
            headers=[(b"x-forwarded-for", b"203.0.113.9")],
        )
    )
    assert resolve_client_ip(request, config) == "10.0.0.1"


def test_forwarded_headers_honored_only_from_trusted_proxy() -> None:
    config = IngressConfig(
        trusted_proxies=["10.0.0.0/8"],
        trust_forwarded_headers=True,
    )
    trusted = Request(
        _scope(
            client="10.1.2.3",
            headers=[(b"x-forwarded-for", b"203.0.113.9, 10.1.2.3")],
        )
    )
    assert resolve_client_ip(trusted, config) == "203.0.113.9"

    spoofed = Request(
        _scope(
            client="198.51.100.7",
            headers=[(b"x-forwarded-for", b"203.0.113.9")],
        )
    )
    assert resolve_client_ip(spoofed, config) == "198.51.100.7"


def test_env_overrides_load_ingress_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCT_FACTORY_TRUSTED_PROXIES", "127.0.0.1,10.0.0.0/8")
    monkeypatch.setenv("PRODUCT_FACTORY_TRUST_FORWARDED", "1")
    monkeypatch.setenv("PRODUCT_FACTORY_MAX_UPLOAD_BYTES", "1024")
    cfg = load_ingress_config({"auth_failure_limit": 3})
    assert cfg.trusted_proxies == ["127.0.0.1", "10.0.0.0/8"]
    assert cfg.trust_forwarded_headers is True
    assert cfg.max_upload_bytes == 1024
    assert cfg.auth_failure_limit == 3


def test_auth_failures_are_rate_limited_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_config = Path(__file__).resolve().parents[2] / "config"
    import shutil

    shutil.copytree(real_config, project / "config")
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_TOKEN", "secret-token")
    monkeypatch.setenv("PRODUCT_FACTORY_OBSERVE_URL", "http://pf.test")
    # Tighten failure limit via policies copy.
    policies = (project / "config" / "policies.yaml").read_text(encoding="utf-8")
    policies = policies.replace("auth_failure_limit: 20", "auth_failure_limit: 3")
    (project / "config" / "policies.yaml").write_text(policies, encoding="utf-8")

    app = create_app(data_dir, project_root=project, observe_base_url="http://pf.test")
    with TestClient(app) as client:
        for _ in range(3):
            denied = client.get(
                "/api/v1/meta",
                headers={"Authorization": "Bearer wrong"},
            )
            assert denied.status_code == 401
        limited = client.get(
            "/api/v1/meta",
            headers={"Authorization": "Bearer wrong"},
        )
        assert limited.status_code == 429
        ok = client.get(
            "/api/v1/meta",
            headers={"Authorization": "Bearer secret-token"},
        )
        # Success path still works for a different outcome after failures, but
        # the failure bucket is independent — reset and confirm success audit.
        INGRESS_LIMITER.reset()
        ok = client.get(
            "/api/v1/meta",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert ok.status_code == 200
        assert "ingress" in ok.json()

    audit = (data_dir / "ops" / "ingress-audit.jsonl").read_text(encoding="utf-8")
    assert "ingress.auth_failed" in audit
    assert "ingress.rate_limited" in audit
    assert "ingress.auth_succeeded" in audit


def test_meta_advertises_ingress_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    import shutil

    shutil.copytree(Path(__file__).resolve().parents[2] / "config", project / "config")
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    monkeypatch.delenv("PRODUCT_FACTORY_OBSERVE_TOKEN", raising=False)
    app = create_app(data_dir, project_root=project)
    with TestClient(app) as client:
        meta = client.get("/api/v1/meta")
    assert meta.status_code == 200
    ingress = meta.json()["ingress"]
    assert ingress["trust_forwarded_headers"] is False
    assert ingress["upload_bounds"]["supported_upload_kinds"] == ["git_bundle"]
