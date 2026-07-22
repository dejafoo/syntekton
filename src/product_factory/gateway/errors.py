"""Gateway errors and retry classification."""

from __future__ import annotations

from product_factory.domain.errors import ProviderError


class RetryableProviderError(ProviderError):
    """Timeout, 429, temporary provider failure."""


class NonRetryableProviderError(ProviderError):
    """Invalid key, unavailable model, policy violation."""


class BudgetRejectedError(NonRetryableProviderError):
    """Request rejected because projected cost exceeds budget."""
