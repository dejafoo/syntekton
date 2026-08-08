"""Policy-bounded repository inventory (SD0.E).

One inventory per repository root + policy digest. Symlinks, path escapes,
prohibited/binary/oversize files, and ceiling breaches never enter prompt
context. SD8 may cache by snapshot revision + policy digest only when
invalidation safety is proven.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

ExclusionReason = Literal[
    "symlink",
    "special_file",
    "path_escape",
    "prohibited",
    "binary",
    "oversize",
    "ceiling_files",
    "ceiling_bytes",
    "ceiling_duration",
    "unreadable",
    "not_admitted",
]


DEFAULT_PROHIBITED_GLOBS: tuple[str, ...] = (
    ".git/**",
    "**/.git/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/node_modules/**",
    "**/dist/**",
    "**/build/**",
    "**/.coverage",
    "**/coverage/**",
    "**/*.pyc",
)

DEFAULT_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        "coverage",
        "secrets",
    }
)


@dataclass(frozen=True, slots=True)
class InventoryPolicy:
    max_files: int = 5_000
    max_file_bytes: int = 100_000
    max_total_bytes: int = 50_000_000
    max_scan_seconds: float = 30.0
    prohibited_globs: tuple[str, ...] = DEFAULT_PROHIBITED_GLOBS
    admit_untracked: bool = False

    def digest(self) -> str:
        payload = (
            f"{self.max_files}:{self.max_file_bytes}:{self.max_total_bytes}:"
            f"{self.max_scan_seconds}:{self.admit_untracked}:"
            f"{','.join(self.prohibited_globs)}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class InventoryExclusion:
    path: str
    reason: ExclusionReason
    detail: str = ""


@dataclass(slots=True)
class InventoryEntry:
    relative_path: str
    size_bytes: int
    sha256: str | None = None


@dataclass(slots=True)
class SafeRepositoryInventory:
    """Enumerated, confined file set for a repository snapshot."""

    root: Path
    policy: InventoryPolicy
    entries: list[InventoryEntry] = field(default_factory=list)
    exclusions: list[InventoryExclusion] = field(default_factory=list)
    truncated: bool = False
    source: Literal["git", "walk"] = "walk"
    scan_seconds: float = 0.0

    @property
    def policy_digest(self) -> str:
        return self.policy.digest()

    def relative_paths(self) -> list[str]:
        return [entry.relative_path for entry in self.entries]

    def contains(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/").lstrip("./")
        return any(entry.relative_path == normalized for entry in self.entries)

    def read_text(self, relative_path: str, *, max_chars: int | None = None) -> str:
        normalized = relative_path.replace("\\", "/").lstrip("./")
        if not self.contains(normalized):
            raise FileNotFoundError(f"Path not admitted by inventory: {normalized}")
        path = self._resolve_admitted(normalized)
        data = path.read_bytes()
        text = data.decode("utf-8")
        if max_chars is not None and len(text) > max_chars:
            return text[:max_chars]
        return text

    def manifest_evidence(self) -> dict[str, Any]:
        return {
            "policy_digest": self.policy_digest,
            "source": self.source,
            "file_count": len(self.entries),
            "total_bytes": sum(e.size_bytes for e in self.entries),
            "truncated": self.truncated,
            "scan_seconds": round(self.scan_seconds, 4),
            "exclusions": [
                {"path": item.path, "reason": item.reason, "detail": item.detail}
                for item in self.exclusions[:200]
            ],
            "exclusion_count": len(self.exclusions),
        }

    def _resolve_admitted(self, relative_path: str) -> Path:
        root = self.root.resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise PermissionError(f"Path escapes repository root: {relative_path}")
        if candidate.is_symlink() or not candidate.is_file():
            raise PermissionError(f"Path is not a regular admitted file: {relative_path}")
        return candidate


def build_safe_repository_inventory(
    root: Path,
    *,
    policy: InventoryPolicy | None = None,
) -> SafeRepositoryInventory:
    root = Path(root)
    policy = policy or InventoryPolicy()
    inventory = SafeRepositoryInventory(root=root, policy=policy)
    if not root.exists():
        inventory.exclusions.append(
            InventoryExclusion(".", "unreadable", "repository root unavailable")
        )
        return inventory

    started = time.monotonic()
    root_resolved = root.resolve()
    candidates, source = _enumerate_candidates(root_resolved, policy=policy, inventory=inventory)
    inventory.source = source

    total_bytes = 0
    for rel in candidates:
        if time.monotonic() - started > policy.max_scan_seconds:
            inventory.truncated = True
            inventory.exclusions.append(
                InventoryExclusion(rel, "ceiling_duration", "scan duration ceiling")
            )
            break
        if len(inventory.entries) >= policy.max_files:
            inventory.truncated = True
            inventory.exclusions.append(
                InventoryExclusion(rel, "ceiling_files", "file count ceiling")
            )
            break

        decision = _classify_path(root_resolved, rel, policy=policy)
        if decision is not None:
            inventory.exclusions.append(decision)
            continue

        abs_path = (root_resolved / rel).resolve()
        try:
            size = abs_path.stat().st_size
        except OSError as exc:
            inventory.exclusions.append(InventoryExclusion(rel, "unreadable", str(exc)))
            continue
        if size > policy.max_file_bytes:
            inventory.exclusions.append(
                InventoryExclusion(rel, "oversize", f"{size}>{policy.max_file_bytes}")
            )
            continue
        if total_bytes + size > policy.max_total_bytes:
            inventory.truncated = True
            inventory.exclusions.append(
                InventoryExclusion(rel, "ceiling_bytes", "total byte ceiling")
            )
            break
        if _looks_binary(abs_path):
            inventory.exclusions.append(InventoryExclusion(rel, "binary"))
            continue

        inventory.entries.append(InventoryEntry(relative_path=rel, size_bytes=size))
        total_bytes += size

    inventory.scan_seconds = time.monotonic() - started
    return inventory


def _enumerate_candidates(
    root: Path,
    *,
    policy: InventoryPolicy,
    inventory: SafeRepositoryInventory,
) -> tuple[list[str], Literal["git", "walk"]]:
    if (root / ".git").exists() or _is_git_workdir(root):
        tracked = _git_ls_files(root)
        if tracked is not None:
            paths = list(tracked)
            if policy.admit_untracked:
                untracked = _git_ls_untracked(root) or []
                paths.extend(untracked)
            return sorted(set(paths)), "git"
        inventory.exclusions.append(
            InventoryExclusion(".", "unreadable", "git enumeration failed; using no-follow walk")
        )
    return _nofollow_walk(root, policy=policy, inventory=inventory), "walk"


def _is_git_workdir(root: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_ls_files(root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [
        item.decode("utf-8", errors="surrogateescape") for item in proc.stdout.split(b"\0") if item
    ]


def _git_ls_untracked(root: Path) -> list[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [
        item.decode("utf-8", errors="surrogateescape") for item in proc.stdout.split(b"\0") if item
    ]


def _nofollow_walk(
    root: Path,
    *,
    policy: InventoryPolicy,
    inventory: SafeRepositoryInventory,
) -> list[str]:
    """os.walk with followlinks=False; never enter symlinked directories."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        # Prune excluded / symlink directories in-place.
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            rel_dir = str(child.relative_to(root)).replace("\\", "/")
            if name in DEFAULT_EXCLUDED_DIR_NAMES or _is_prohibited(rel_dir + "/", policy):
                inventory.exclusions.append(InventoryExclusion(rel_dir, "prohibited"))
                continue
            if child.is_symlink():
                inventory.exclusions.append(InventoryExclusion(rel_dir, "symlink", "directory"))
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            rel = str(child.relative_to(root)).replace("\\", "/")
            found.append(rel)
    return sorted(found)


def _classify_path(
    root: Path,
    rel: str,
    *,
    policy: InventoryPolicy,
) -> InventoryExclusion | None:
    rel_norm = rel.replace("\\", "/").lstrip("./")
    if not rel_norm or rel_norm.startswith("../") or "/../" in f"/{rel_norm}/":
        return InventoryExclusion(rel_norm, "path_escape")
    if _is_prohibited(rel_norm, policy):
        return InventoryExclusion(rel_norm, "prohibited")
    abs_path = root / rel_norm
    try:
        if abs_path.is_symlink():
            return InventoryExclusion(rel_norm, "symlink")
        resolved = abs_path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            return InventoryExclusion(rel_norm, "path_escape", "canonical escape")
        if not resolved.is_file():
            if resolved.exists():
                return InventoryExclusion(rel_norm, "special_file")
            return InventoryExclusion(rel_norm, "unreadable", "missing")
        if not resolved.is_file() or resolved.is_symlink():
            return InventoryExclusion(rel_norm, "special_file")
    except OSError as exc:
        return InventoryExclusion(rel_norm, "unreadable", str(exc))
    return None


def _is_prohibited(rel: str, policy: InventoryPolicy) -> bool:
    rel_norm = rel.replace("\\", "/")
    parts = set(Path(rel_norm).parts)
    if parts & DEFAULT_EXCLUDED_DIR_NAMES:
        return True
    for pattern in policy.prohibited_globs:
        if fnmatch(rel_norm, pattern) or fnmatch(rel_norm, pattern.rstrip("/")):
            return True
    return False


def _looks_binary(path: Path, *, sample_size: int = 8192) -> bool:
    try:
        chunk = path.read_bytes()[:sample_size]
    except OSError:
        return True
    if b"\0" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


@dataclass(frozen=True, slots=True)
class InventoryCacheKey:
    """Cache key is snapshot revision + policy digest only (SD8)."""

    snapshot_revision: str
    policy_digest: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.snapshot_revision, self.policy_digest)


class SafeInventoryCache:
    """Process-local inventory cache keyed by snapshot + policy digest.

    Entries are never reused across different revisions or policies. Changing
    either key forces a rebuild so prohibited/stale paths cannot leak.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, str], SafeRepositoryInventory] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: InventoryCacheKey) -> SafeRepositoryInventory | None:
        with self._lock:
            inventory = self._entries.get(key.as_tuple())
            if inventory is None:
                self.misses += 1
                return None
            self.hits += 1
            return inventory

    def put(self, key: InventoryCacheKey, inventory: SafeRepositoryInventory) -> None:
        if inventory.policy_digest != key.policy_digest:
            raise ValueError("inventory policy digest does not match cache key")
        with self._lock:
            self._entries[key.as_tuple()] = inventory

    def invalidate(self, *, snapshot_revision: str | None = None) -> None:
        with self._lock:
            if snapshot_revision is None:
                self._entries.clear()
                return
            doomed = [key for key in self._entries if key[0] == snapshot_revision]
            for key in doomed:
                del self._entries[key]

    def get_or_build(
        self,
        *,
        root: Path,
        snapshot_revision: str,
        policy: InventoryPolicy | None = None,
    ) -> SafeRepositoryInventory:
        policy = policy or InventoryPolicy()
        key = InventoryCacheKey(
            snapshot_revision=snapshot_revision,
            policy_digest=policy.digest(),
        )
        cached = self.get(key)
        if cached is not None:
            return cached
        inventory = build_safe_repository_inventory(root, policy=policy)
        self.put(key, inventory)
        return inventory


__all__ = [
    "DEFAULT_EXCLUDED_DIR_NAMES",
    "DEFAULT_PROHIBITED_GLOBS",
    "InventoryCacheKey",
    "InventoryEntry",
    "InventoryExclusion",
    "InventoryPolicy",
    "SafeInventoryCache",
    "SafeRepositoryInventory",
    "build_safe_repository_inventory",
]
