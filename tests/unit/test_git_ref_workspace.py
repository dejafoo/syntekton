from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from product_factory.config.repositories import RepositoriesConfig, RepositoryEntry
from product_factory.domain.errors import ConfigurationError, UnsafeOperationError
from product_factory.domain.runs import GitRefWorkspace
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.workspace import WorkspaceManager


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "fixture@example.com")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "README.md").write_text("pinned\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _manager(tmp_path: Path, repo: Path) -> WorkspaceManager:
    config = RepositoriesConfig(
        repositories={
            "sample_api": RepositoryEntry(
                fetch_url=str(repo),
                refs=["refs/heads/main"],
            )
        }
    )
    return WorkspaceManager(config, tmp_path / "workspaces")


def test_prepare_git_ref_resolves_exact_commit_and_detached_checkout(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    prepared = _manager(tmp_path, repo).prepare(
        GitRefWorkspace(repository_id="sample_api", ref="refs/heads/main"),
        workspace_id="run-1",
    )

    assert prepared.path.is_dir()
    assert _git(prepared.path, "rev-parse", "HEAD") == commit
    assert _git(prepared.path, "status", "--porcelain") == ""
    assert prepared.provenance.model_dump() == {
        "kind": "git_ref",
        "repository_id": "sample_api",
        "ref": "refs/heads/main",
        "commit": commit,
    }


def test_prepare_rejects_floating_ref_and_commit_mismatch(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)
    manager = _manager(tmp_path, repo)

    with pytest.raises(ConfigurationError, match="explicit, non-default"):
        manager.prepare(
            GitRefWorkspace(repository_id="sample_api", ref="HEAD"),
            workspace_id="run-head",
        )
    with pytest.raises(ConfigurationError, match="not allowed"):
        manager.prepare(
            GitRefWorkspace(repository_id="sample_api", ref="refs/heads/release"),
            workspace_id="run-release",
        )
    with pytest.raises(ConfigurationError, match="does not match"):
        manager.prepare(
            GitRefWorkspace(
                repository_id="sample_api",
                ref="refs/heads/main",
                commit="0" * 40,
            ),
            workspace_id="run-mismatch",
        )


def test_workspace_and_task_worktree_paths_cannot_escape(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path)
    manager = _manager(tmp_path, repo)
    with pytest.raises(UnsafeOperationError, match="Unsafe workspace_id"):
        manager.prepare(
            GitRefWorkspace(repository_id="sample_api", ref="refs/heads/main"),
            workspace_id="../escape",
        )

    worktrees = WorktreeManager(repo, tmp_path / "task-worktrees")
    with pytest.raises(UnsafeOperationError, match="escapes root"):
        worktrees.create("../escape", base_commit=commit)
    assert not (tmp_path / "escape").exists()
