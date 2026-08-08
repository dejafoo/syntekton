"""Trusted registries for capabilities, adapters, and related policy."""

from __future__ import annotations

from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    CapabilityDescriptor,
    EvaluationCategory,
    agent_profile_for,
    default_budget_for,
    descriptor_for,
    evaluation_category_for,
    executor_adapter_for,
    executor_mode_for,
    model_role_for,
    permissible_tool_classes_for,
    require_descriptor,
    result_schema_for,
    validate_descriptor_catalog,
)

__all__ = [
    "CAPABILITY_DESCRIPTORS",
    "CapabilityDescriptor",
    "EvaluationCategory",
    "agent_profile_for",
    "default_budget_for",
    "descriptor_for",
    "evaluation_category_for",
    "executor_adapter_for",
    "executor_mode_for",
    "model_role_for",
    "permissible_tool_classes_for",
    "require_descriptor",
    "result_schema_for",
    "validate_descriptor_catalog",
]
