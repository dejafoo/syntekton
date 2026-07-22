"""Tests for request-aware implementation and patch extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

from product_factory.domain.tools import CapabilityGrant
from product_factory.orchestration.coordinator import (
    deterministic_impl_files,
    extract_unified_diff,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.repositories.patches import create_patch
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


def test_extract_unified_diff_raw_and_fenced() -> None:
    raw = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+hi\n"
    assert extract_unified_diff(raw).startswith("diff --git")
    fenced = "Here you go:\n```diff\n" + raw + "```\n"
    assert extract_unified_diff(fenced).startswith("diff --git")
    assert extract_unified_diff("no patch here") == ""


def test_deterministic_impl_files_cache_vs_health() -> None:
    cache_files = dict(deterministic_impl_files("Introduce a simple cache helper"))
    assert "src/app/cache.py" in cache_files
    assert "InMemoryCache" in cache_files["src/app/cache.py"]
    health_files = dict(deterministic_impl_files("Add a validated health-check endpoint"))
    assert "src/app/health.py" in health_files


def test_git_diff_includes_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True
    )
    (repo / "README").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    store = ArtifactStore(tmp_path / "artifacts")
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=store,
        worktree_root=repo,
        base_commit=base,
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            agent_profile="implementation_worker",
            tool_names={"create_file", "git_diff"},
            allowed_path_patterns=["**/*"],
            max_calls=10,
        )
    )
    broker.execute(
        task_id="t1",
        tool_name="create_file",
        arguments={"path": "src/app/cache.py", "content": "x=1\n", "overwrite": True},
    )
    diff = broker.execute(task_id="t1", tool_name="git_diff", arguments={})
    assert "cache.py" in diff["patch"]
    assert diff["patch"].strip()
    # create_patch helper agrees
    assert "cache.py" in create_patch(repo, base)
