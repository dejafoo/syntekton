"""Typed error hierarchy for Product Factory."""

from __future__ import annotations


class ProductFactoryError(Exception):
    """Base error for all Product Factory failures."""

    exit_code: int = 1

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(ProductFactoryError):
    exit_code = 2


class PlanRejectedError(ProductFactoryError):
    exit_code = 3


class RuntimeFailureError(ProductFactoryError):
    exit_code = 4


class ValidationFailureError(ProductFactoryError):
    exit_code = 5


class BudgetExhaustedError(ProductFactoryError):
    exit_code = 6


class ApprovalBlockedError(ProductFactoryError):
    exit_code = 7


class RunCancelledError(ProductFactoryError):
    """Raised when a cooperative cancel flag is observed between waves/tasks."""

    exit_code = 9


class UnsafeOperationError(ProductFactoryError):
    exit_code = 8


class ToolAuthorizationError(UnsafeOperationError):
    exit_code = 8


class SkillGrantViolation(UnsafeOperationError):
    """Raised when a task's tool grant conflicts with a matched skill's declared policy."""

    exit_code = 8


class ProviderError(RuntimeFailureError):
    exit_code = 4


class SchemaValidationError(ValidationFailureError):
    exit_code = 5
