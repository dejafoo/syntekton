"""Construct configured gateway adapters and the routing layer."""

from __future__ import annotations

import os
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.errors import ConfigurationError
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.gateway.openai_compatible import OpenAICompatibleGateway
from product_factory.gateway.openrouter import OpenRouterGateway
from product_factory.gateway.router import RoutingGateway


def gateway_from_config(config: AppConfig, *, force_mock: bool = False) -> ModelGateway:
    """Build the local/cloud router while preserving forced-mock behavior."""
    if force_mock or os.environ.get("PRODUCT_FACTORY_FORCE_MOCK"):
        return MockGateway()

    profiles = {
        name: profile.model_dump(mode="python")
        for name, profile in config.models.profiles.items()
    }
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

    return RoutingGateway(
        profiles=profiles,
        profile_gateways=profile_gateways,
        adapter_gateways=adapter_gateways,
    )


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
