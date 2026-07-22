"""Model gateway base interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from product_factory.gateway.canonical_messages import ModelRequest, ModelResponse


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
