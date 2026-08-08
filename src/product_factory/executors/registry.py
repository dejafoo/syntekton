"""Task executor registry — dispatch from persisted executor_mode (SD1.B)."""

from __future__ import annotations

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.tasks import TaskResult
from product_factory.executors.composition import CompositionExecutor
from product_factory.executors.deterministic import DeterministicExecutor
from product_factory.executors.interface_agent import InterfaceAgentExecutor
from product_factory.executors.model_draft import ModelDraftExecutor
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    TaskExecutor,
    blocked_result,
)
from product_factory.executors.repository_agent import RepositoryAgentExecutor
from product_factory.executors.research_agent import ResearchAgentExecutor
from product_factory.executors.validation import TestExecutionExecutor
from product_factory.registry.capability_descriptors import CAPABILITY_DESCRIPTORS


class TaskExecutorRegistry:
    """Maps executor_mode → registered TaskExecutor."""

    def __init__(self) -> None:
        self._by_mode: dict[str, TaskExecutor] = {}

    def register(self, executor: TaskExecutor) -> None:
        mode = executor.executor_mode
        if mode in self._by_mode:
            raise ConfigurationError(f"Duplicate executor registration for mode {mode!r}")
        self._by_mode[mode] = executor

    def require(self, executor_mode: str) -> TaskExecutor:
        try:
            return self._by_mode[executor_mode]
        except KeyError as exc:
            raise ConfigurationError(
                f"No task executor registered for mode {executor_mode!r}"
            ) from exc

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        persisted_mode = request.effective_policy.executor_mode
        descriptor_mode = request.descriptor.executor_mode
        if persisted_mode != descriptor_mode:
            return blocked_result(
                request,
                summary=(
                    f"executor_mode mismatch: policy={persisted_mode!r} "
                    f"descriptor={descriptor_mode!r}"
                ),
                activity={"reason": "executor_mode_mismatch"},
            )
        try:
            executor = self.require(persisted_mode)
        except ConfigurationError:
            return blocked_result(
                request,
                summary=f"unsupported executor_mode: {persisted_mode}",
                activity={"reason": "unknown_executor_mode"},
            )
        adapter = request.descriptor.executor_adapter_id
        if adapter not in executor.adapter_ids:
            return blocked_result(
                request,
                summary=f"adapter {adapter!r} not owned by mode {persisted_mode!r}",
                activity={"reason": "adapter_not_registered"},
            )
        return executor.execute(request)

    def modes(self) -> frozenset[str]:
        return frozenset(self._by_mode)


def default_executor_registry() -> TaskExecutorRegistry:
    registry = TaskExecutorRegistry()
    registry.register(DeterministicExecutor())
    registry.register(RepositoryAgentExecutor())
    registry.register(ResearchAgentExecutor())
    registry.register(InterfaceAgentExecutor())
    registry.register(ModelDraftExecutor())
    registry.register(TestExecutionExecutor())
    registry.register(CompositionExecutor())
    # Fail closed if a descriptor mode lacks a registered executor.
    missing = {
        descriptor.executor_mode
        for descriptor in CAPABILITY_DESCRIPTORS.values()
        if descriptor.executor_mode not in registry.modes()
    }
    if missing:
        raise ConfigurationError(f"Descriptor executor modes lack adapters: {sorted(missing)}")
    return registry


_DEFAULT: TaskExecutorRegistry | None = None


def execute_task(request: TaskExecutionRequest) -> TaskResult:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = default_executor_registry()
    return _DEFAULT.execute(request)
