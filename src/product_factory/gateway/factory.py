"""Construct configured gateway adapters and the routing layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.errors import ConfigurationError
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.circuit_breaker import CircuitBreaker
from product_factory.gateway.evidence import LocalRouteEvidenceStore
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.openrouter import OpenRouterGateway
from product_factory.gateway.probes import LocalRouteController
from product_factory.gateway.router import RoutingGateway

# Optional cutover override: apply to every local openai_compatible profile.
_LOCAL_BASE_URL_ENV = "PRODUCT_FACTORY_LOCAL_BASE_URL"


def gateway_from_config(
    config: AppConfig,
    *,
    force_mock: bool = False,
    evidence_root: Path | None = None,
    run_startup_probes: bool = True,
) -> ModelGateway:
    """Build the local/cloud router while preserving forced-mock behavior."""
    if force_mock or os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"):
        return MockGateway()

    profiles = {
        name: profile.model_dump(mode="python") for name, profile in config.models.profiles.items()
    }
    local_base_url = os.environ.get(_LOCAL_BASE_URL_ENV, "").strip()
    if local_base_url:
        for profile in profiles.values():
            if (
                profile.get("route_class") == "local"
                and profile.get("provider_adapter") == "openai_compatible"
            ):
                profile["base_url"] = local_base_url
                # Loopback AMD runtimes often omit bearer auth.
                if os.environ.get("PRODUCT_FACTORY_LOCAL_API_KEY_ENV"):
                    profile["api_key_env"] = os.environ["PRODUCT_FACTORY_LOCAL_API_KEY_ENV"]
                elif profile.get("api_key_env") == "OPENROUTER_API_KEY":
                    profile["api_key_env"] = None

    if not _has_routable_credentials(profiles):
        return MockGateway()

    openrouter = OpenRouterGateway(profile_models=profiles)
    mock = MockGateway()
    adapter_gateways: dict[str, ModelGateway] = {
        "openrouter": openrouter,
        "mock": mock,
    }
    profile_gateways: dict[str, ModelGateway] = {}

    for name, profile in profiles.items():
        adapter = profile["provider_adapter"]
        if adapter == "openai_compatible":
            base_url = profile.get("base_url")
            if not base_url:
                raise ConfigurationError(
                    f"Model profile {name!r} requires base_url for openai_compatible"
                )
            profile_gateways[name] = OpenAICompatibleGateway(
                base_url=str(base_url),
                api_key_env=profile.get("api_key_env"),
                profile_models=profiles,
            )
        elif adapter == "mock":
            profile_gateways[name] = MockGateway(
                catalog=[
                    {
                        "id": profile["model"],
                        "capabilities": list(profile.get("capabilities") or []),
                        "context_length": profile.get("context_soft_limit"),
                    }
                ],
                default_model=str(profile["model"]),
            )
        else:
            try:
                profile_gateways[name] = adapter_gateways[adapter]
            except KeyError as exc:
                raise ConfigurationError(
                    f"Unsupported provider_adapter {adapter!r} for profile {name!r}"
                ) from exc

    for name, profile in profiles.items():
        policy = profile.get("cloud_fallback") or {}
        target = policy.get("profile")
        if policy.get("enabled") and target and target not in profiles:
            raise ConfigurationError(
                f"Model profile {name!r} references unknown fallback profile {target!r}"
            )

    evidence_store: LocalRouteEvidenceStore | None = None
    root = evidence_root
    if root is None:
        candidate = config.root / ".product-factory"
        if candidate.exists():
            root = candidate
    if root is not None:
        evidence_store = LocalRouteEvidenceStore(root)

    route_controllers: dict[str, LocalRouteController] = {}
    for name, profile in profiles.items():
        if profile.get("route_class") != "local":
            continue
        if profile.get("provider_adapter") not in {"openai_compatible", "mock"}:
            continue
        probe_cfg = profile.get("probe") or {}
        breaker_cfg = profile.get("circuit_breaker") or {}
        controller = LocalRouteController(
            profile_name=name,
            profile=profile,
            gateway=profile_gateways[name],
            breaker=CircuitBreaker(
                failure_threshold=int(breaker_cfg.get("failure_threshold", 3)),
                recovery_timeout_s=float(breaker_cfg.get("recovery_timeout_s", 60.0)),
            ),
            light_ttl_s=float(probe_cfg.get("light_ttl_s", 30.0)),
            deep_interval_s=float(probe_cfg.get("deep_interval_s", 300.0)),
            max_probe_latency_ms=int(probe_cfg.get("max_latency_ms", 30_000)),
            enable_deep_probes=bool(probe_cfg.get("deep", True)),
            evidence_sink=evidence_store.record if evidence_store is not None else None,
        )
        route_controllers[name] = controller

    router = RoutingGateway(
        profiles=profiles,
        profile_gateways=profile_gateways,
        adapter_gateways=adapter_gateways,
        route_controllers=route_controllers,
    )
    if run_startup_probes and route_controllers:
        # Conservative: light probes at construction; deep probes are periodic
        # and also forced by explicit probe_local_routes() / live evaluation.
        for controller in route_controllers.values():
            try:
                controller.ensure_report(force_deep=False)
            except Exception:
                # Startup must not crash the host when the local runtime is down;
                # routing will fall back or deny per profile policy.
                controller.record_failure()
    return router


def _has_routable_credentials(profiles: dict[str, dict[str, Any]]) -> bool:
    """Retain the historical no-credential fallback to deterministic mock."""
    for profile in profiles.values():
        adapter = profile["provider_adapter"]
        if adapter == "mock":
            continue
        env_name = profile.get("api_key_env")
        if env_name and os.environ.get(str(env_name)):
            return True
        if adapter == "openai_compatible" and not env_name:
            return True
        if adapter == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
            return True
    return False
