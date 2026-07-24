"""Build a :class:`~product_factory.host.service.HostService` for the MCP process."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.config.loader import load_config
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


def build_host_service(
    *,
    mock: bool | None = None,
    data_dir: Path | None = None,
    project_root: Path | None = None,
) -> HostService:
    """Construct HostService from env / cwd (same defaults as host CLI)."""
    force_mock = (
        bool(mock)
        if mock is not None
        else bool(os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"))
    )
    root = project_root or Path.cwd()
    config = load_config(root)
    if data_dir is None and os.environ.get("PRODUCT_FACTORY_DATA_DIR"):
        data_dir = Path(os.environ["PRODUCT_FACTORY_DATA_DIR"])
    gateway = _gateway_from_config(config, force_mock=force_mock)
    return HostService(
        config=config,
        gateway=gateway,
        data_dir=data_dir,
        use_deterministic_planner=force_mock or isinstance(gateway, MockGateway),
    )
