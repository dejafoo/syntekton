"""Remote-mode helpers for the host/observe API (PM2.B1)."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.config.loader import find_project_root
from product_factory.config.repositories import RepositoriesConfig, load_repositories_config


def configured_control_token() -> str | None:
    """Bearer token for control + observe when configured.

    `PRODUCT_FACTORY_OBSERVE_TOKEN` is canonical; `PRODUCT_FACTORY_HOST_TOKEN`
    is accepted as an alias (R0 docs / operator sketches).
    """
    return (
        os.environ.get("PRODUCT_FACTORY_OBSERVE_TOKEN")
        or os.environ.get("PRODUCT_FACTORY_HOST_TOKEN")
        or None
    )


def remote_mode_enabled() -> bool:
    """Opt-in remote gate. Local CLI submit stays unchanged when unset/false."""
    raw = (os.environ.get("PRODUCT_FACTORY_REMOTE_MODE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def canonical_observe_base(*, request_base: str | None = None) -> str:
    env = (os.environ.get("PRODUCT_FACTORY_OBSERVE_URL") or "").strip().rstrip("/")
    if env:
        return env
    if request_base:
        return request_base.rstrip("/")
    return "http://127.0.0.1:8765"


def resolve_project_root(
    *,
    data_dir: Path,
    project_root: Path | None = None,
) -> Path:
    """Resolve config/project root for observe + remote registry loading.

    Order: explicit ``project_root`` → ``PRODUCT_FACTORY_ROOT`` → walk from
    ``data_dir.parent`` (local default: ``<repo>/.product-factory``).
    """
    if project_root is not None:
        return project_root.expanduser().resolve()
    env = (os.environ.get("PRODUCT_FACTORY_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return find_project_root(data_dir.parent)


def repositories_for_root(project_root: Path) -> RepositoriesConfig:
    config_dir = project_root / "config"
    if not config_dir.exists():
        alt = project_root / ".product-factory" / "config"
        if alt.exists():
            config_dir = alt
    return load_repositories_config(config_dir)
