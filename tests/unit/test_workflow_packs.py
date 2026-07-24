"""Unit tests for the workflow pack registry (P1.G)."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows import (
    WorkflowPack,
    canonical_workflow_id,
    list_workflow_packs,
    resolve_workflow_pack,
)
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK


def test_registry_lists_repository_change() -> None:
    packs = list_workflow_packs()
    assert any(p.id == "repository_change" for p in packs)


def test_resolve_repository_change_directly() -> None:
    pack = resolve_workflow_pack("repository_change")
    assert pack is REPOSITORY_CHANGE_PACK
    assert pack.version == "1.0.0"


def test_code_change_aliases_to_repository_change() -> None:
    assert canonical_workflow_id("code_change") == "repository_change"
    pack = resolve_workflow_pack("code_change")
    assert pack.id == "repository_change"


def test_unknown_workflow_id_fails_closed() -> None:
    with pytest.raises(ConfigurationError):
        resolve_workflow_pack("totally_unknown_workflow")


def test_pack_hash_is_stable_and_present_in_manifest_metadata() -> None:
    pack = resolve_workflow_pack("repository_change")
    meta = pack.manifest_metadata()
    assert meta["workflow_pack_id"] == "repository_change"
    assert meta["workflow_pack_version"] == "1.0.0"
    assert meta["workflow_pack_hash"] == pack.content_hash()
    # Deterministic across calls.
    assert pack.content_hash() == pack.content_hash()


def test_pack_hash_changes_when_content_changes() -> None:
    base = resolve_workflow_pack("repository_change")
    mutated = WorkflowPack(
        id=base.id,
        version=base.version,
        input_schema=base.input_schema,
        output_schema=base.output_schema,
        allowed_capabilities=base.allowed_capabilities,
        default_planner_mode=base.default_planner_mode,
        validation_policy={**base.validation_policy, "extra": True},
        skill_policy=base.skill_policy,
        routing_defaults=base.routing_defaults,
    )
    assert mutated.content_hash() != base.content_hash()


def test_repository_change_wraps_code_change_capabilities() -> None:
    pack = resolve_workflow_pack("repository_change")
    assert "implementation" in pack.allowed_capabilities
    assert "independent_review" in pack.allowed_capabilities
    assert "composition" in pack.allowed_capabilities


def test_pack_never_carries_executable_code_only_data() -> None:
    pack = resolve_workflow_pack("repository_change")
    for field in (
        pack.input_schema,
        pack.output_schema,
        pack.validation_policy,
        pack.skill_policy,
        pack.routing_defaults,
    ):
        assert isinstance(field, dict)
