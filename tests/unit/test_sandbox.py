"""Unit tests for sandboxed command env scrubbing (SR0 nested uv cache)."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.tools.sandbox import (
    _ENV_ALLOWLIST,
    ManagedSandboxPaths,
    SandboxResult,
    _scrubbed_env,
    classify_command_result,
)


def test_scrubbed_env_uses_managed_uv_cache_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    paths = ManagedSandboxPaths.under(tmp_path / ".product-factory")
    env = _scrubbed_env(managed_paths=paths)
    assert "UV_CACHE_DIR" in env
    assert env["UV_CACHE_DIR"] == str((tmp_path / ".product-factory/cache/tooling/uv").resolve())
    assert Path(env["UV_CACHE_DIR"]).is_dir()


def test_scrubbed_env_refuses_inherited_uv_cache_dir(tmp_path: Path, monkeypatch) -> None:
    explicit = tmp_path / "custom-uv-cache"
    explicit.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(explicit))
    env = _scrubbed_env(managed_paths=ManagedSandboxPaths.under(tmp_path / ".product-factory"))
    assert env["UV_CACHE_DIR"] != str(explicit)


def test_uv_cache_dir_is_not_allowlisted() -> None:
    assert "UV_CACHE_DIR" not in _ENV_ALLOWLIST


def test_managed_paths_reject_home_or_filesystem_root() -> None:
    with pytest.raises(ValueError):
        ManagedSandboxPaths.under(Path.home())
    with pytest.raises(ValueError):
        ManagedSandboxPaths.under(Path("/"))


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, "passed"), (1, "domain_failure"), (2, "unavailable"), (124, "timeout")],
)
def test_registered_command_result_classification_is_explicit(
    returncode: int, expected: str
) -> None:
    result = SandboxResult(returncode, "", "", 0.0, "restricted")
    assert classify_command_result(result, {"domain_failure_exit_codes": [1]}) == expected
