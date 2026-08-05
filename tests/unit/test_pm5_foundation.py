"""Shared PM5 capability, authority, schema, and artifact contracts."""

from __future__ import annotations

import pytest

from product_factory.domain.capabilities import (
    CAPABILITIES,
    CAPABILITY_TOOL_CLASSES,
    DEPLOYMENT_TOOL_CLASSES,
    EXTERNAL_READ_TOOL_CLASSES,
)
from product_factory.domain.errors import ConfigurationError, SchemaValidationError
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.scheduling.scheduler import select_model
from product_factory.schemas import (
    ROLE_TO_SCHEMA,
    assert_schema_writable,
    default_schema_registry,
    resolve_output_schema_id,
    validate_write_payload,
)
from product_factory.workflows.artifacts import (
    DEPLOYMENT_RECORD_LAND_SPEC,
    OPERATIONAL_RECORD_LAND_SPEC,
    RELEASE_PLAN_LAND_SPEC,
    ROLE_DEPLOYMENT_RECORD,
    ROLE_OPERATIONAL_RECORD,
    ROLE_RELEASE_PLAN,
    resolve_artifact_land_map,
)
from product_factory.workflows.base import DEFAULT_EXECUTOR_MODES
from product_factory.workflows.handlers.base import validate_handler_authority


def _task(capability: str) -> TaskSpec:
    return TaskSpec(
        id="T-PM5",
        title="PM5 foundation",
        capability=capability,  # type: ignore[arg-type]
        objective="Exercise shared PM5 scheduling",
        expected_output_schema="release_plan.v1",
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-PM5",
                description="Foundation is available",
                verification="artifact_check",
            )
        ],
    )


def test_pm5_capabilities_have_isolated_tool_class_maps() -> None:
    assert {"release_analysis", "operations_analysis", "deployment_execution"} <= CAPABILITIES
    assert {"ci_read", "ops_read"} <= EXTERNAL_READ_TOOL_CLASSES
    assert {"ci_read", "ops_read"} <= CAPABILITY_TOOL_CLASSES["release_analysis"]
    assert CAPABILITY_TOOL_CLASSES["operations_analysis"] == {
        "artifact_write",
        "ops_read",
    }
    assert CAPABILITY_TOOL_CLASSES["deployment_execution"] >= DEPLOYMENT_TOOL_CLASSES

    for capability, tool_classes in CAPABILITY_TOOL_CLASSES.items():
        if capability != "deployment_execution":
            assert not (tool_classes & DEPLOYMENT_TOOL_CLASSES)


def test_pm5_scheduler_and_executor_defaults_are_explicit() -> None:
    assert select_model(_task("release_analysis")) == "fast_worker"
    assert select_model(_task("operations_analysis")) == "fast_worker"
    assert select_model(_task("deployment_execution")) == "supervisor"
    assert DEFAULT_EXECUTOR_MODES["release_analysis"] == "model_draft"
    assert DEFAULT_EXECUTOR_MODES["operations_analysis"] == "model_draft"
    assert DEFAULT_EXECUTOR_MODES["deployment_execution"] == "deterministic"


def test_external_write_authority_is_reserved_for_deployment() -> None:
    validate_handler_authority(
        "deployment_execution",
        "external_write",
        approval_required=True,
    )
    with pytest.raises(ConfigurationError, match="approval-gated"):
        validate_handler_authority("deployment_execution", "external_write")
    with pytest.raises(ConfigurationError, match="reserved"):
        validate_handler_authority(
            "release_readiness",
            "external_write",
            approval_required=True,
        )


def test_pm5_schemas_are_writable_and_role_mapped() -> None:
    registry = default_schema_registry()
    for schema_id, role in (
        ("release_plan.v1", ROLE_RELEASE_PLAN),
        ("deployment_record.v1", ROLE_DEPLOYMENT_RECORD),
        ("operational_record.v1", ROLE_OPERATIONAL_RECORD),
    ):
        spec = registry.require(schema_id, for_write=True)
        assert spec.kind == "task_output"
        assert spec.reserved is False
        assert assert_schema_writable(schema_id) == schema_id
        assert ROLE_TO_SCHEMA[role] == schema_id

    assert resolve_output_schema_id("release_plan.document.v1") == "release_plan.v1"
    assert resolve_output_schema_id("deployment_record.document.v1") == "deployment_record.v1"
    assert resolve_output_schema_id("operational_record.document.v1") == "operational_record.v1"


@pytest.mark.parametrize(
    "schema_id",
    ["release_plan.v1", "deployment_record.v1", "operational_record.v1"],
)
def test_pm5_schema_validation_fails_closed_on_incomplete_payload(
    schema_id: str,
) -> None:
    with pytest.raises(SchemaValidationError, match="missing required fields"):
        validate_write_payload(schema_id, {})


def test_pm5_land_specs_share_safe_json_defaults() -> None:
    specs = (
        RELEASE_PLAN_LAND_SPEC,
        DEPLOYMENT_RECORD_LAND_SPEC,
        OPERATIONAL_RECORD_LAND_SPEC,
    )
    land_map = resolve_artifact_land_map(specs)
    assert {entry.role for entry in land_map.entries} == {
        ROLE_RELEASE_PLAN,
        ROLE_DEPLOYMENT_RECORD,
        ROLE_OPERATIONAL_RECORD,
    }
    assert all(entry.media_type == "application/json" for entry in land_map.entries)
    assert all(entry.dest_path.startswith("docs/") for entry in land_map.entries)
