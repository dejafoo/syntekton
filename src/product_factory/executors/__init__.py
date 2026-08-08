"""Registered task executors (SD1)."""

from __future__ import annotations

from product_factory.executors.protocol import TaskExecutionRequest
from product_factory.executors.registry import default_executor_registry, execute_task

__all__ = [
    "TaskExecutionRequest",
    "default_executor_registry",
    "execute_task",
]
