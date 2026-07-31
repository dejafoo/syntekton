"""Server-side repository registry for registered paths and git-ref workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError


class RepositoryEntry(BaseModel):
    path: Path | None = None
    fetch_url: str | None = None
    credential_ref: str | None = None
    refs: list[str] = Field(default_factory=list)
    description: str = ""


class RepositoriesConfig(BaseModel):
    repositories: dict[str, RepositoryEntry] = Field(default_factory=dict)

    def ids(self) -> list[str]:
        return sorted(self.repositories)

    def resolve(self, repository_id: str) -> Path:
        entry = self.entry(repository_id)
        if entry.path is None:
            raise ConfigurationError(
                f"repository_id {repository_id!r} has no registered path",
                details={"repository_id": repository_id},
            )
        path = entry.path.expanduser()
        if not path.is_absolute():
            raise ConfigurationError(
                f"repository_id {repository_id!r} path must be absolute: {path}",
                details={"repository_id": repository_id, "path": str(path)},
            )
        return path.resolve()

    def entry(self, repository_id: str) -> RepositoryEntry:
        entry = self.repositories.get(repository_id)
        if entry is None:
            known = ", ".join(self.ids()) or "(none)"
            raise ConfigurationError(
                f"Unknown repository_id {repository_id!r}; known: {known}",
                details={"repository_id": repository_id, "known": self.ids()},
            )
        return entry

    def fetch_url(self, repository_id: str) -> str:
        entry = self.entry(repository_id)
        if entry.fetch_url:
            return entry.fetch_url
        if entry.path is not None:
            return str(entry.path.expanduser().resolve())
        raise ConfigurationError(
            f"repository_id {repository_id!r} has no fetch_url",
            details={"repository_id": repository_id},
        )


def load_repositories_config(config_dir: Path) -> RepositoriesConfig:
    """Load `repositories.yaml` if present; missing file → empty registry."""
    path = config_dir / "repositories.yaml"
    if not path.exists():
        return RepositoriesConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config root must be a mapping: {path}")
    raw_repos = data.get("repositories") or {}
    if not isinstance(raw_repos, dict):
        raise ConfigurationError(f"repositories must be a mapping: {path}")
    normalized: dict[str, Any] = {"repositories": {}}
    for repo_id, entry in raw_repos.items():
        if isinstance(entry, str):
            normalized["repositories"][str(repo_id)] = {"path": entry}
        elif isinstance(entry, dict):
            normalized["repositories"][str(repo_id)] = entry
        else:
            raise ConfigurationError(f"Invalid repository entry for {repo_id!r} in {path}")
    try:
        return RepositoriesConfig.model_validate(normalized)
    except Exception as exc:
        raise ConfigurationError(f"Failed to load repositories config: {exc}") from exc
