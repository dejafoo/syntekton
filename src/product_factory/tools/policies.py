"""Path scope enforcement for tool broker."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from product_factory.domain.errors import ToolAuthorizationError


def resolve_under_root(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ToolAuthorizationError(
            f"Path escapes allowed root: {relative}",
            details={"root": str(root_resolved), "path": relative},
        ) from exc
    if candidate.is_symlink():
        target = candidate.resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise ToolAuthorizationError(
                f"Symlink escapes allowed root: {relative}",
                details={"root": str(root_resolved), "path": relative},
            ) from exc
    return candidate


def path_allowed(relative: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    norm = relative.lstrip("./")
    if any(pattern in {"**", "**/*"} for pattern in patterns):
        return True
    if norm in {"", "."} and any(pat in {"**/*", "*", "**"} for pat in patterns):
        return True
    return any(
        fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(Path(norm).name, pat) for pat in patterns
    )


def assert_path_allowed(relative: str, patterns: list[str]) -> None:
    if not path_allowed(relative, patterns):
        raise ToolAuthorizationError(
            f"Path not in allowlist: {relative}",
            details={"patterns": patterns},
        )
