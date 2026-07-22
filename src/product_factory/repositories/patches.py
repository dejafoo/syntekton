"""Patch helpers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from product_factory.domain.errors import ValidationFailureError

_DIFF_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
_DIFF_OLD_PATH_RE = re.compile(r"^--- a/(.+)$", re.MULTILINE)


def changed_paths_from_patch(patch: str) -> list[str]:
    """Return unique repository-relative paths touched by a unified diff."""
    paths: list[str] = []
    seen: set[str] = set()
    for match in _DIFF_PATH_RE.finditer(patch):
        path = match.group(1).strip()
        if path == "/dev/null" or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    if not paths:
        for match in _DIFF_OLD_PATH_RE.finditer(patch):
            path = match.group(1).strip()
            if path == "/dev/null" or path in seen:
                continue
            seen.add(path)
            paths.append(path)
    return paths


def detect_writer_conflicts(
    owned_paths: dict[str, str],
    candidate_paths: list[str],
    writer_task_id: str,
) -> list[dict[str, str]]:
    """Return conflicts when a writer touches a path owned by another task."""
    conflicts: list[dict[str, str]] = []
    for path in candidate_paths:
        owner = owned_paths.get(path)
        if owner is not None and owner != writer_task_id:
            conflicts.append(
                {
                    "path": path,
                    "owner_task_id": owner,
                    "conflicting_task_id": writer_task_id,
                }
            )
        else:
            owned_paths[path] = writer_task_id
    return conflicts


def create_patch(worktree: Path, base_commit: str) -> str:
    """Create a unified diff vs base_commit, including previously untracked files."""
    # Intent-to-add so brand-new files appear in `git diff` (tracked-only otherwise).
    subprocess.run(
        ["git", "add", "-N", "--", "."],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    result = subprocess.run(
        ["git", "diff", base_commit],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ValidationFailureError(f"git diff failed: {result.stderr.strip()}")
    return result.stdout


def apply_patch_check(repository: Path, patch: str, *, base_commit: str | None = None) -> bool:
    """Return True if patch applies cleanly (dry-run)."""
    proc = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=repository,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def apply_patch(repository: Path, patch: str) -> None:
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=repository,
        input=patch,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValidationFailureError(f"Patch apply failed: {proc.stderr.strip()}")
