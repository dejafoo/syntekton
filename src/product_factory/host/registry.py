"""Process-local HostService ownership keyed by data root.

SD4.A: prevent multiple mock/live HostService instances from supervising the
same data root. Callers share one application service per resolved root.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.factory import gateway_from_config
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService

_LOCK = threading.RLock()
_SERVICES: dict[Path, HostService] = {}


def resolve_data_root(config: AppConfig, data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        return data_dir.expanduser().resolve()
    env = (os.environ.get("PRODUCT_FACTORY_DATA_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (config.root / ".product-factory").resolve()


def get_host_service(
    *,
    config: AppConfig,
    gateway: ModelGateway | None = None,
    data_dir: Path | None = None,
    use_deterministic_planner: bool | None = None,
    observe_base_url: str | None = None,
    force_mock: bool = False,
) -> HostService:
    """Return the sole HostService for this data root.

    The first construction locks mock/live mode for the process. Later callers
    reuse that supervisor instead of opening a second connection against the
    same SQLite root (SD4.A).
    """
    root = resolve_data_root(config, data_dir)
    want_mock = bool(
        force_mock
        or os.environ.get("PRODUCT_FACTORY_FORCE_MOCK")
        or (gateway is not None and isinstance(gateway, MockGateway))
    )
    with _LOCK:
        existing = _SERVICES.get(root)
        if existing is not None:
            if observe_base_url:
                existing.observe_base_url = observe_base_url.rstrip("/")
            return existing

        if gateway is None:
            gateway = gateway_from_config(config, force_mock=want_mock)
        planner = (
            use_deterministic_planner
            if use_deterministic_planner is not None
            else (want_mock or isinstance(gateway, MockGateway))
        )
        service = HostService(
            config=config,
            gateway=gateway,
            data_dir=root,
            use_deterministic_planner=planner,
            observe_base_url=observe_base_url,
        )
        _SERVICES[root] = service
        return service


def close_host_service(data_dir: Path | None = None, *, config: AppConfig | None = None) -> None:
    """Close and forget a registered HostService (tests / process shutdown)."""
    with _LOCK:
        if data_dir is None and config is None:
            roots = list(_SERVICES)
        elif data_dir is not None:
            roots = [data_dir.expanduser().resolve()]
        else:
            assert config is not None
            roots = [resolve_data_root(config)]
        for root in roots:
            service = _SERVICES.pop(root, None)
            if service is not None:
                service.close()


def reset_host_registry() -> None:
    """Test helper: close every registered service."""
    close_host_service()


def registered_roots() -> list[Path]:
    with _LOCK:
        return list(_SERVICES)


def host_service_snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            str(root): {
                "mock": isinstance(svc.gateway, MockGateway) or svc.use_deterministic_planner,
                "observe_base_url": svc.observe_base_url,
            }
            for root, svc in _SERVICES.items()
        }
