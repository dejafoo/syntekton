"""Build a :class:`~product_factory.host.service.HostService` for the MCP process."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.config.loader import find_project_root, load_config
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openrouter import OpenRouterGateway
from product_factory.host.service import HostService


def _gateway_from_config(config, *, force_mock: bool = False):
    if force_mock:
        return MockGateway()
    profiles = {
        name: {
            "model": p.model,
            "pricing": p.pricing,
            "provider": p.provider,
        }
        for name, p in config.models.profiles.items()
    }
    if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get(
        "PRODUCT_FACTORY_FORCE_MOCK"
    ):
        return OpenRouterGateway(profile_models=profiles)
    return MockGateway()


def _package_config_root() -> Path | None:
    """Locate shipped ``config/models.yaml`` relative to this install (editable checkout)."""
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "config" / "models.yaml").exists():
            return candidate
    return None


def _has_pf_config(root: Path) -> bool:
    return (root / "config" / "models.yaml").exists() or (
        root / ".product-factory" / "config" / "models.yaml"
    ).exists()


def resolve_mcp_config_root(project_root: Path | None = None) -> Path:
    """Resolve Product Factory config root for MCP (cwd-independent).

    Order: explicit ``project_root`` → ``PRODUCT_FACTORY_ROOT`` → cwd project with
    config → package/checkout config. OpenCode often starts MCP with a cwd that
    is not the PF repo; falling back to the install tree avoids immediate exit
    (``Connection closed``).
    """
    if project_root is not None:
        return project_root.expanduser().resolve()
    env = os.environ.get("PRODUCT_FACTORY_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cwd_root = find_project_root(Path.cwd())
    if _has_pf_config(cwd_root):
        return cwd_root
    packaged = _package_config_root()
    if packaged is not None:
        return packaged
    return Path.cwd().resolve()


def build_host_service(
    *,
    mock: bool | None = None,
    data_dir: Path | None = None,
    project_root: Path | None = None,
) -> HostService:
    """Construct HostService from env / cwd / package config."""
    force_mock = (
        bool(mock)
        if mock is not None
        else bool(os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"))
    )
    root = resolve_mcp_config_root(project_root)
    config = load_config(root)
    if data_dir is None and os.environ.get("PRODUCT_FACTORY_DATA_DIR"):
        data_dir = Path(os.environ["PRODUCT_FACTORY_DATA_DIR"]).expanduser()
    elif data_dir is None:
        cwd_pf = Path.cwd() / ".product-factory"
        if cwd_pf.is_dir() and root.resolve() != Path.cwd().resolve():
            # Prefer the host project's data dir when MCP cwd is a different app.
            data_dir = cwd_pf
    gateway = _gateway_from_config(config, force_mock=force_mock)
    return HostService(
        config=config,
        gateway=gateway,
        data_dir=data_dir,
        use_deterministic_planner=force_mock or isinstance(gateway, MockGateway),
    )
