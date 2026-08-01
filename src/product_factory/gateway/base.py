"""Model gateway base interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse


@dataclass(frozen=True)
class GatewayProbe:
    """Adapter readiness and model capability result used by the router."""

    healthy: bool
    model_available: bool = True
    capabilities: frozenset[str] = field(default_factory=frozenset)
    reason: str | None = None

    def supports(self, required: set[str]) -> bool:
        return self.healthy and self.model_available and required.issubset(self.capabilities)


class ModelGateway(ABC):
    """Provider-neutral chat completion interface."""

    @abstractmethod
    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    @abstractmethod
    def refresh_catalog(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def probe(
        self,
        *,
        model: str,
        required_capabilities: set[str] | None = None,
    ) -> GatewayProbe:
        """Probe adapter health and model availability.

        Adapters may override this for a provider-specific health endpoint. The
        default uses the OpenAI-compatible models catalogue.
        """
        required = required_capabilities or set()
        try:
            models = self.list_models()
        except Exception as exc:
            return GatewayProbe(
                healthy=False,
                model_available=False,
                reason=f"{type(exc).__name__}: {exc}",
            )
        entry = next((item for item in models if item.get("id") == model), None)
        if entry is None:
            return GatewayProbe(
                healthy=True,
                model_available=False,
                reason=f"model {model!r} is not advertised",
            )
        advertised = entry.get("capabilities")
        capabilities = (
            frozenset(str(value) for value in advertised)
            if isinstance(advertised, list)
            else frozenset(required)
        )
        return GatewayProbe(
            healthy=True,
            model_available=True,
            capabilities=capabilities,
        )
