"""API dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.config.loader import AppConfig, find_project_root, load_config
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openrouter import OpenRouterGateway
from product_factory.host.service import HostService
from product_factory.observability.query import ObservabilityQueryService
from product_factory.persistence.database import Database


def _gateway_from_config(config: AppConfig, *, force_mock: bool = False) -> ModelGateway:
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
    if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"):
        return OpenRouterGateway(profile_models=profiles)
    return MockGateway()


class ApiState:
    def __init__(
        self,
        data_dir: Path,
        *,
        project_root: Path | None = None,
        observe_base_url: str | None = None,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.project_root = project_root
        self.observe_base_url = observe_base_url
        self.db = Database(self.data_dir / "data" / "product_factory.sqlite")
        self.query = ObservabilityQueryService(self.db, data_dir=self.data_dir)
        self._hosts: dict[bool, HostService] = {}

    def config(self) -> AppConfig:
        root = self.project_root or find_project_root(self.data_dir.parent)
        return load_config(root)

    def host(
        self,
        *,
        mock: bool = False,
        observe_base_url: str | None = None,
    ) -> HostService:
        """Shared HostService for control routes (cached per mock flag)."""
        force_mock = mock or bool(os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"))
        if force_mock not in self._hosts:
            config = self.config()
            gateway = _gateway_from_config(config, force_mock=force_mock)
            self._hosts[force_mock] = HostService(
                config=config,
                gateway=gateway,
                data_dir=self.data_dir,
                use_deterministic_planner=force_mock or isinstance(gateway, MockGateway),
                observe_base_url=observe_base_url or self.observe_base_url or None,
            )
        service = self._hosts[force_mock]
        base = observe_base_url or self.observe_base_url
        if base:
            service.observe_base_url = base.rstrip("/")
        return service

    def close(self) -> None:
        for service in self._hosts.values():
            service.coord.db.close()
        self._hosts.clear()
        self.db.close()
