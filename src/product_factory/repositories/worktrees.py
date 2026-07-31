"""Git worktree isolation for implementation tasks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from product_factory.domain.errors import ConfigurationError, UnsafeOperationError


@dataclass
class Worktree:
    task_id: str
    path: Path
    base_commit: str
    writable: bool


class WorktreeManager:
    def __init__(self, repository_path: Path, worktrees_root: Path) -> None:
        self.repository_path = repository_path.resolve()
        self.worktrees_root = worktrees_root
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, Worktree] = {}

    def _path_for(self, task_id: str) -> Path:
        path = self.worktrees_root / task_id
        try:
            path.resolve().relative_to(self.worktrees_root.resolve())
        except ValueError as exc:
            raise UnsafeOperationError(f"Worktree task id escapes root: {task_id!r}") from exc
        if path.parent.resolve() != self.worktrees_root.resolve():
            raise UnsafeOperationError(f"Worktree task id must be one path component: {task_id!r}")
        return path

    def create(self, task_id: str, *, base_commit: str, writable: bool = True) -> Worktree:
        if task_id in self._active:
            raise UnsafeOperationError(f"Worktree already exists for task {task_id}")
        path = self._path_for(task_id)
        if path.exists():
            raise UnsafeOperationError(f"Worktree path already exists: {path}")

        result = subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "--detach",
                str(path),
                base_commit,
            ],
            cwd=self.repository_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ConfigurationError(
                f"Failed to create worktree: {result.stderr.strip() or result.stdout.strip()}"
            )

        wt = Worktree(task_id=task_id, path=path, base_commit=base_commit, writable=writable)
        self._active[task_id] = wt
        return wt

    def get(self, task_id: str) -> Worktree:
        if task_id not in self._active:
            raise KeyError(task_id)
        return self._active[task_id]

    def exists_on_disk(self, task_id: str) -> bool:
        return self._path_for(task_id).exists()

    def reattach(self, task_id: str, *, base_commit: str, writable: bool = True) -> Worktree:
        """Re-register a worktree directory left on disk by a prior process (resume, P1.B).

        Does not re-run `git worktree add` — the directory already exists.
        """
        path = self._path_for(task_id)
        if not path.exists():
            raise KeyError(task_id)
        wt = Worktree(task_id=task_id, path=path, base_commit=base_commit, writable=writable)
        self._active[task_id] = wt
        return wt

    def remove(self, task_id: str, *, force: bool = False) -> None:
        wt = self._active.pop(task_id, None)
        if wt is None:
            return
        args = ["git", "worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(wt.path))
        subprocess.run(args, cwd=self.repository_path, capture_output=True, text=True, check=False)

    def cleanup(self, *, force: bool = False) -> None:
        for task_id in list(self._active):
            self.remove(task_id, force=force)
