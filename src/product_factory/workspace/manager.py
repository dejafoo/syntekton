"""Prepare commit-pinned, server-owned git workspaces."""

from __future__ import annotations

import fnmatch
import hashlib
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from product_factory.config.repositories import RepositoriesConfig
from product_factory.domain.errors import ConfigurationError, UnsafeOperationError
from product_factory.domain.runs import GitRefWorkspace, WorkspaceProvenance

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FLOATING_REFS = frozenset({"HEAD", "FETCH_HEAD", "ORIG_HEAD", "@", "@{upstream}"})
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class PreparedWorkspace:
    path: Path
    provenance: WorkspaceProvenance


def _run_git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ConfigurationError(f"Git workspace preparation failed: {detail}")
    return result.stdout.strip()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class WorkspaceManager:
    """Resolve a registry ref into an immutable commit and detached run checkout."""

    def __init__(self, registry: RepositoriesConfig, root: Path) -> None:
        self.registry = registry
        self.root = root.resolve()
        self.mirrors_root = self.root / "mirrors"
        self.runs_root = self.root / "runs"
        self.mirrors_root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def prepare(self, spec: GitRefWorkspace, *, workspace_id: str) -> PreparedWorkspace:
        self._validate_component(spec.repository_id, field="repository_id")
        self._validate_component(workspace_id, field="workspace_id")
        requested_ref = self._validate_ref(spec.ref)
        entry = self.registry.entry(spec.repository_id)
        self._validate_allowed_ref(requested_ref, entry.refs)
        fetch_url = self.registry.fetch_url(spec.repository_id)

        mirror_key = hashlib.sha256(spec.repository_id.encode()).hexdigest()[:20]
        mirror = self.mirrors_root / f"{mirror_key}.git"
        checkout = self.runs_root / workspace_id
        self._assert_child(checkout, self.runs_root)

        with _lock_for(str(mirror)):
            if not mirror.exists():
                _run_git("init", "--bare", str(mirror))
                _run_git("--git-dir", str(mirror), "remote", "add", "origin", fetch_url)
            else:
                _run_git("--git-dir", str(mirror), "remote", "set-url", "origin", fetch_url)
            _run_git(
                "--git-dir",
                str(mirror),
                "fetch",
                "--force",
                "--no-tags",
                "origin",
                requested_ref,
            )
            resolved = _run_git(
                "--git-dir", str(mirror), "rev-parse", "--verify", "FETCH_HEAD^{commit}"
            ).lower()
            if spec.commit is not None:
                expected = spec.commit.lower()
                if not _COMMIT_RE.fullmatch(expected):
                    raise ConfigurationError("workspace.commit must be a full 40-character SHA")
                if expected != resolved:
                    raise ConfigurationError(
                        "workspace.commit does not match the fetched ref",
                        details={
                            "repository_id": spec.repository_id,
                            "ref": requested_ref,
                            "expected_commit": expected,
                            "resolved_commit": resolved,
                        },
                    )
            if checkout.exists():
                raise UnsafeOperationError(f"Workspace path already exists: {checkout}")
            try:
                _run_git(
                    "--git-dir",
                    str(mirror),
                    "worktree",
                    "add",
                    "--detach",
                    str(checkout),
                    resolved,
                )
            except Exception:
                shutil.rmtree(checkout, ignore_errors=True)
                raise

        return PreparedWorkspace(
            path=checkout.resolve(),
            provenance=WorkspaceProvenance(
                repository_id=spec.repository_id,
                ref=requested_ref,
                commit=resolved,
            ),
        )

    @staticmethod
    def _validate_component(value: str, *, field: str) -> None:
        if not _SAFE_COMPONENT_RE.fullmatch(value):
            raise UnsafeOperationError(f"Unsafe {field}: {value!r}")

    @staticmethod
    def _validate_ref(ref: str) -> str:
        value = ref.strip()
        if not value or value in _FLOATING_REFS or value.startswith("-"):
            raise ConfigurationError(
                "workspace.ref must be an explicit, non-default git ref",
                details={"ref": ref},
            )
        result = subprocess.run(
            ["git", "check-ref-format", "--allow-onelevel", value],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ConfigurationError(f"Invalid workspace.ref: {ref!r}")
        return value

    @staticmethod
    def _validate_allowed_ref(ref: str, allowed: list[str]) -> None:
        if not allowed:
            raise ConfigurationError("Repository does not allow any git refs")
        candidates = {ref}
        if not ref.startswith("refs/"):
            candidates.add(f"refs/heads/{ref}")
            candidates.add(f"refs/tags/{ref}")
        if not any(
            fnmatch.fnmatchcase(candidate, pattern)
            for candidate in candidates
            for pattern in allowed
        ):
            raise ConfigurationError(
                f"workspace.ref {ref!r} is not allowed by the repository registry",
                details={"ref": ref, "allowed_refs": allowed},
            )

    @staticmethod
    def _assert_child(path: Path, parent: Path) -> None:
        try:
            path.resolve().relative_to(parent.resolve())
        except ValueError as exc:
            raise UnsafeOperationError(f"Workspace path escapes root: {path}") from exc
