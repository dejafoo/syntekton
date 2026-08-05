"""API dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.api.ingress import IngressAuditor, IngressConfig, load_ingress_config
from product_factory.api.remote_mode import resolve_project_root
from product_factory.config.loader import AppConfig, load_config
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.factory import gateway_from_config
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.observability.query import ObservabilityQueryService
from product_factory.persistence.database import Database
from product_factory.workspace.uploads import UploadStore


def _gateway_from_config(config: AppConfig, *, force_mock: bool = False) -> ModelGateway:
    return gateway_from_config(config, force_mock=force_mock)


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
        self._ingress_config: IngressConfig | None = None
        self._ingress_auditor = IngressAuditor(self.data_dir / "ops" / "ingress-audit.jsonl")
        self._upload_store: UploadStore | None = None

    def config(self) -> AppConfig:
        root = resolve_project_root(data_dir=self.data_dir, project_root=self.project_root)
        return load_config(root)

    def ingress_config(self) -> IngressConfig:
        if self._ingress_config is None:
            try:
                raw = self.config().policies.ingress
            except Exception:
                raw = {}
            self._ingress_config = load_ingress_config(raw)
        return self._ingress_config

    def ingress_auditor(self) -> IngressAuditor:
        return self._ingress_auditor

    def upload_store(self) -> UploadStore:
        if self._upload_store is None:
            self._upload_store = UploadStore(self.data_dir, self.ingress_config())
        return self._upload_store

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
            service.close()
        self._hosts.clear()
        self.db.close()
