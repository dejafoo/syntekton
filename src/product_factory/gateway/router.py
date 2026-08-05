"""Policy-driven local/cloud model gateway router."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from product_factory.domain.errors import ProviderError
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse
from product_factory.gateway.errors import (
    BudgetRejectedError,
    NonRetryableProviderError,
)
from product_factory.gateway.pricing import estimate_cost
from product_factory.gateway.probes import LocalRouteController


class RoutingGateway(ModelGateway):
    """Select a configured route and permit only explicit cloud fallbacks."""

    def __init__(
        self,
        *,
        profiles: dict[str, dict[str, Any]],
        profile_gateways: dict[str, ModelGateway],
        adapter_gateways: dict[str, ModelGateway],
        route_controllers: dict[str, LocalRouteController] | None = None,
    ) -> None:
        self.profiles = profiles
        self.profile_gateways = profile_gateways
        self.adapter_gateways = adapter_gateways
        self.route_controllers = route_controllers or {}

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        profile = self._profile(request.model_profile)
        self._check_request_budget(request, profile)
        gateway = self.profile_gateways[request.model_profile]
        controller = self.route_controllers.get(request.model_profile)
        reason = self._probe_reason(
            gateway,
            profile,
            profile_name=request.model_profile,
            controller=controller,
        )

        if reason is None:
            try:
                response = gateway.complete(request)
            except BudgetRejectedError:
                raise
            except ProviderError:
                if profile.get("route_class") != "local":
                    raise
                if controller is not None:
                    controller.record_failure()
                reason = "provider_error"
            else:
                if controller is not None:
                    controller.record_success()
                return self._annotate(
                    response,
                    profile,
                    None,
                    started,
                    primary_profile=request.model_profile,
                    controller=controller,
                )

        if profile.get("route_class") != "local":
            raise NonRetryableProviderError(
                f"Configured cloud route {request.model_profile!r} is unavailable: {reason}"
            )
        return self._fallback(
            request,
            profile,
            reason,
            started,
            primary_profile=request.model_profile,
            controller=controller,
        )

    def _fallback(
        self,
        request: ModelRequest,
        profile: dict[str, Any],
        reason: str,
        started: float,
        *,
        primary_profile: str,
        controller: LocalRouteController | None,
    ) -> ModelResponse:
        policy = profile.get("cloud_fallback") or {}
        allowed = set(policy.get("allowed_reasons") or [])
        if not policy.get("enabled") or reason not in allowed:
            raise NonRetryableProviderError(
                f"Cloud fallback denied for profile {request.model_profile!r}: {reason}"
            )

        fallback_profile_name = policy.get("profile")
        adapter_name = policy.get("adapter")
        if fallback_profile_name:
            fallback_profile = self._profile(fallback_profile_name)
            if fallback_profile.get("route_class") != "cloud":
                raise NonRetryableProviderError(
                    f"Fallback profile {fallback_profile_name!r} is not a cloud route"
                )
            gateway = self.profile_gateways[fallback_profile_name]
            fallback_request = request.model_copy(update={"model_profile": fallback_profile_name})
        elif adapter_name:
            fallback_profile = {**profile, "route_class": "cloud"}
            try:
                gateway = self.adapter_gateways[adapter_name]
            except KeyError as exc:
                raise NonRetryableProviderError(
                    f"Fallback adapter {adapter_name!r} is not configured"
                ) from exc
            fallback_request = request
            fallback_profile_name = None
        else:
            raise NonRetryableProviderError(
                f"Cloud fallback for {request.model_profile!r} has no profile or adapter"
            )

        self._check_request_budget(request, fallback_profile)
        fallback_probe_reason = self._probe_reason(
            gateway,
            fallback_profile,
            profile_name=fallback_profile_name or request.model_profile,
            controller=None,
        )
        if fallback_probe_reason is not None:
            raise NonRetryableProviderError(
                f"Cloud fallback unavailable for {request.model_profile!r}: {fallback_probe_reason}"
            )
        response = gateway.complete(fallback_request)
        return self._annotate(
            response,
            fallback_profile,
            reason,
            started,
            primary_profile=primary_profile,
            fallback_profile=fallback_profile_name,
            controller=controller,
        )

    @staticmethod
    def _check_request_budget(
        request: ModelRequest,
        profile: dict[str, Any],
    ) -> None:
        if request.max_cost_usd is not None and request.max_cost_usd <= 0:
            raise BudgetRejectedError("Request rejected by routing budget guard")
        if request.max_cost_usd is None:
            return
        pricing = profile.get("pricing") or {}
        input_tokens = sum(max(1, len(message.content) // 4) for message in request.messages)
        projected = estimate_cost(
            input_tokens=input_tokens,
            output_tokens=request.max_output_tokens,
            input_price_per_million=pricing.get("input", "0"),
            output_price_per_million=pricing.get("output", "0"),
            cached_input_price_per_million=pricing.get("cached_input"),
        )
        if projected > Decimal(str(request.max_cost_usd)):
            raise BudgetRejectedError(
                f"Projected route cost ${projected} exceeds request ceiling ${request.max_cost_usd}"
            )

    def _probe_reason(
        self,
        gateway: ModelGateway,
        profile: dict[str, Any],
        *,
        profile_name: str,
        controller: LocalRouteController | None,
    ) -> str | None:
        if controller is not None:
            return controller.evaluate()

        # Legacy light probe for cloud/adapters without a local controller.
        # Advertised capability lists are trusted only when present; a missing
        # field is not treated as proof for local routes.
        required = set(profile.get("capabilities") or [])
        probe = gateway.probe(
            model=str(profile["model"]),
            required_capabilities=required,
        )
        if not probe.healthy:
            return "local_unhealthy"
        if not probe.model_available:
            return "capability_miss"
        if profile.get("route_class") == "local" and required and not probe.supports(required):
            return "capability_miss"
        if profile.get("route_class") != "local" and required and not probe.supports(required):
            # Cloud catalogues often omit custom capability tags; only fail when
            # the provider explicitly advertised a conflicting set.
            advertised = probe.capabilities
            if advertised and not required.issubset(advertised):
                return "capability_miss"
        return None

    @staticmethod
    def _annotate(
        response: ModelResponse,
        profile: dict[str, Any],
        fallback_reason: str | None,
        started: float,
        *,
        primary_profile: str,
        fallback_profile: str | None = None,
        controller: LocalRouteController | None = None,
    ) -> ModelResponse:
        usage = response.usage
        cost_basis = "reported" if usage.reported_cost_usd is not None else "estimated"
        routing: dict[str, Any] = {
            "route": profile.get("route_class", "cloud"),
            "provider": response.provider,
            "model": response.resolved_model_id,
            "primary_profile": primary_profile,
            "fallback_profile": fallback_profile,
            "fallback_reason": fallback_reason,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "cost_basis": cost_basis,
            "cost_usd": str(
                usage.reported_cost_usd
                if usage.reported_cost_usd is not None
                else usage.estimated_cost_usd
            ),
        }
        if controller is not None:
            routing["circuit"] = controller.breaker.snapshot()
            if controller.last_report is not None:
                routing["admission_proven"] = sorted(controller.last_report.proven)
        return response.model_copy(update={"routing": routing})

    def _profile(self, name: str) -> dict[str, Any]:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise NonRetryableProviderError(f"Unknown model profile {name!r}") from exc

    def refresh_catalog(self) -> dict[str, Any]:
        return {name: gateway.refresh_catalog() for name, gateway in self.profile_gateways.items()}

    def list_models(self) -> list[dict[str, Any]]:
        models: dict[str, dict[str, Any]] = {}
        for gateway in self.profile_gateways.values():
            for model in gateway.list_models():
                if "id" in model:
                    models[str(model["id"])] = model
        return list(models.values())

    def probe_local_routes(self, *, force_deep: bool = True) -> dict[str, Any]:
        """Run startup/periodic probes for every local controller."""
        results: dict[str, Any] = {}
        for name, controller in self.route_controllers.items():
            report = controller.ensure_report(force_deep=force_deep)
            results[name] = {
                **controller.snapshot(),
                "report": report.as_dict(),
            }
        return results
