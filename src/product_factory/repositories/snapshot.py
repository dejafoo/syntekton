"""Repository snapshot management."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from product_factory.domain.errors import ConfigurationError, UnsafeOperationError


@dataclass
class RepositorySnapshot:
    repository_path: Path
    base_commit: str
    is_dirty: bool
    manifest: dict


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ConfigurationError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def snapshot_repository(
    repository_path: Path,
    *,
    allow_dirty: bool = False,
    output_dir: Path | None = None,
) -> RepositorySnapshot:
    repo = repository_path.resolve()
    if not (repo / ".git").exists() and not _is_git_repo(repo):
        raise ConfigurationError(f"Not a git repository: {repo}")

    base_commit = _run_git(repo, "rev-parse", "HEAD")
    status = _run_git(repo, "status", "--porcelain")
    is_dirty = bool(status)
    if is_dirty and not allow_dirty:
        raise UnsafeOperationError(
            "Repository has uncommitted changes; refuse snapshot "
            "(set allow_dirty_repo or clean the tree)."
        )

    files = _run_git(repo, "ls-files").splitlines()
    manifest = {
        "repository_path": str(repo),
        "base_commit": base_commit,
        "is_dirty": is_dirty,
        "file_count": len(files),
        "files": files[:500],
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "repository-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (output_dir / "base-commit.txt").write_text(base_commit + "\n", encoding="utf-8")

    return RepositorySnapshot(
        repository_path=repo,
        base_commit=base_commit,
        is_dirty=is_dirty,
        manifest=manifest,
    )


def _is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"
