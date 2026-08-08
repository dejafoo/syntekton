"""API dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from product_factory.api.ingress import IngressAuditor, IngressConfig, load_ingress_config
from product_factory.api.remote_mode import resolve_project_root
from product_factory.config.loader import AppConfig, load_config
from product_factory.host.registry import close_host_service, get_host_service
from product_factory.host.service import HostService
from product_factory.observability.query import ObservabilityQueryService
from product_factory.persistence.database import Database
from product_factory.workspace.uploads import UploadStore


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
        self._host: HostService | None = None
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
        """Sole HostService for this API process data root (SD4.A).

        Mock vs live is resolved once. A conflicting second request refuses
        rather than opening a dual supervisor on the same SQLite root.
        """
        force_mock = mock or bool(os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"))
        base = observe_base_url or self.observe_base_url
        self._host = get_host_service(
            config=self.config(),
            data_dir=self.data_dir,
            force_mock=force_mock,
            observe_base_url=base,
        )
        return self._host

    def close(self) -> None:
        close_host_service(self.data_dir)
        self._host = None
        self.db.close()
