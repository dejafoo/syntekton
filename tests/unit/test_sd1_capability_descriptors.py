"""SD1.A/E — capability descriptor and executor registry completeness."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from product_factory.context.assembler import AGENT_PROFILES
from product_factory.domain.capabilities import CAPABILITIES, CAPABILITY_TOOL_CLASSES
from product_factory.domain.errors import ConfigurationError
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    KNOWN_AGENT_PROFILES,
    KNOWN_EVALUATION_CATEGORIES,
    KNOWN_EXECUTOR_ADAPTERS,
    KNOWN_MODEL_ROLES,
    require_descriptor,
    validate_descriptor_catalog,
)
from product_factory.workflows.base import DEFAULT_EXECUTOR_MODES, EXECUTOR_MODES, execution_policy
from product_factory.workflows.registry import list_workflow_packs


def test_descriptor_catalog_covers_every_capability() -> None:
    assert set(CAPABILITY_DESCRIPTORS) == set(CAPABILITIES)
    validate_descriptor_catalog()


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_every_capability_has_complete_descriptor(capability: str) -> None:
    descriptor = require_descriptor(capability)
    assert descriptor.executor_mode in EXECUTOR_MODES
    assert descriptor.executor_adapter_id in KNOWN_EXECUTOR_ADAPTERS
    assert descriptor.agent_profile_id in KNOWN_AGENT_PROFILES
    assert descriptor.default_model_role in KNOWN_MODEL_ROLES
    assert descriptor.evaluation_category in KNOWN_EVALUATION_CATEGORIES
    assert descriptor.result_schema_id
    assert descriptor.parser_id
    assert descriptor.permissible_tool_classes
    assert DEFAULT_EXECUTOR_MODES[capability] == descriptor.executor_mode
    assert CAPABILITY_TOOL_CLASSES[capability] == descriptor.permissible_tool_classes
    assert descriptor.agent_profile_id in AGENT_PROFILES


def test_no_implementation_worker_fallback_for_release_ops_deploy() -> None:
    assert require_descriptor("release_analysis").agent_profile_id == "release_analyst"
    assert require_descriptor("operations_analysis").agent_profile_id == "operations_analyst"
    assert require_descriptor("deployment_execution").agent_profile_id == "deployment_controller"
    for profile in ("release_analyst", "operations_analyst", "deployment_controller"):
        assert profile in AGENT_PROFILES


def test_unknown_capability_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="Unknown capability"):
        require_descriptor("not_a_real_capability")


def test_pack_cannot_widen_tool_authority() -> None:
    with pytest.raises(ConfigurationError, match="widened_tool_authority"):
        execution_policy(
            capabilities=frozenset({"documentation"}),
            validators=["document_sections"],
            output_roles=("architecture_document",),
            allowed_tool_classes=frozenset({"documentation", "repository_write"}),  # type: ignore[arg-type]
        ).validate(pack_id="bogus", capabilities=frozenset({"documentation"}))


def test_pack_cannot_override_executor_mode() -> None:
    policy = execution_policy(
        capabilities=frozenset({"documentation"}),
        validators=["document_sections"],
        output_roles=("architecture_document",),
    )
    # Force a mismatched mode after construction.
    object.__setattr__(
        policy,
        "executor_modes",
        {"documentation": "repository_agent_loop"},
    )
    with pytest.raises(ConfigurationError, match="executor_mode_mismatches"):
        policy.validate(pack_id="bogus", capabilities=frozenset({"documentation"}))


def test_registered_packs_validate_against_descriptors() -> None:
    for pack in list_workflow_packs():
        pack.execution_policy.validate(
            pack_id=pack.id, capabilities=pack.allowed_capabilities
        )


def test_coordinator_has_no_completed_stub_fallback() -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = (
        root / "src" / "product_factory" / "orchestration" / "coordinator.py"
    ).read_text(encoding="utf-8")
    assert "completed (stub)" not in coordinator
    # Also ensure no string-literal stub remains under executors/.
    for path in (root / "src" / "product_factory" / "executors").rglob("*.py"):
        assert "completed (stub)" not in path.read_text(encoding="utf-8")


def test_executor_package_defines_registry_module() -> None:
    root = Path(__file__).resolve().parents[2]
    registry_path = root / "src" / "product_factory" / "executors" / "registry.py"
    assert registry_path.exists()
    tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    assert "TaskExecutorRegistry" in names or "default_executor_registry" in {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
