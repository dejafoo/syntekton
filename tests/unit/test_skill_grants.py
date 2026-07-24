"""Unit tests for skill-tool grant enforcement (P1.E)."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.domain.errors import SkillGrantViolation
from product_factory.orchestration.skill_grants import (
    enforce_skill_grants,
    resolve_broker_tool_names,
)
from product_factory.skills.registry import Skill, SkillManifest


def _skill(**overrides: object) -> Skill:
    defaults: dict[str, object] = {
        "id": "test.skill",
        "version": "1.0.0",
        "title": "Test Skill",
        "capabilities": ["implementation"],
        "required_tools": [],
        "prohibited_tools": [],
    }
    defaults.update(overrides)
    manifest = SkillManifest.model_validate(defaults)
    return Skill(manifest=manifest, content="", path=Path("."))


def test_resolve_broker_tool_names_tool_class() -> None:
    assert resolve_broker_tool_names("repository_read") == {
        "read_file",
        "list_files",
        "search_text",
    }


def test_resolve_broker_tool_names_concrete_tool_passthrough() -> None:
    assert resolve_broker_tool_names("git_diff") == {"git_diff"}


def test_resolve_broker_tool_names_network_access_always_empty() -> None:
    assert resolve_broker_tool_names("network_access") == frozenset()


def test_resolve_broker_tool_names_unknown_name_fails_closed_to_empty() -> None:
    assert resolve_broker_tool_names("totally_unknown_tool_name") == frozenset()


def test_matching_required_and_absent_prohibited_passes() -> None:
    skill = _skill(required_tools=["repository_read", "git_diff"], prohibited_tools=["network_access"])
    enforce_skill_grants(
        skills=[skill], granted_tool_names={"read_file", "list_files", "search_text", "git_diff"}
    )


def test_required_tool_not_granted_fails_closed() -> None:
    skill = _skill(required_tools=["repository_write"])
    with pytest.raises(SkillGrantViolation) as excinfo:
        enforce_skill_grants(skills=[skill], granted_tool_names={"read_file"})
    assert excinfo.value.details["required_tool"] == "repository_write"


def test_required_tool_unresolvable_fails_closed() -> None:
    skill = _skill(required_tools=["network_access"])
    with pytest.raises(SkillGrantViolation):
        enforce_skill_grants(skills=[skill], granted_tool_names={"read_file", "list_files"})


def test_prohibited_tool_present_in_grant_fails_closed() -> None:
    skill = _skill(prohibited_tools=["repository_write"])
    with pytest.raises(SkillGrantViolation) as excinfo:
        enforce_skill_grants(skills=[skill], granted_tool_names={"read_file", "create_file"})
    assert "create_file" in excinfo.value.details["overlap"]


def test_prohibited_network_access_never_violated_by_current_tool_surface() -> None:
    skill = _skill(prohibited_tools=["network_access"])
    enforce_skill_grants(
        skills=[skill],
        granted_tool_names={"read_file", "list_files", "search_text", "create_file", "apply_patch"},
    )


def test_multiple_skills_all_checked() -> None:
    reader = _skill(id="a", required_tools=["repository_read"])
    writer = _skill(id="b", prohibited_tools=["repository_write"])
    with pytest.raises(SkillGrantViolation):
        enforce_skill_grants(
            skills=[reader, writer], granted_tool_names={"read_file", "create_file"}
        )


def test_no_skills_never_raises() -> None:
    enforce_skill_grants(skills=[], granted_tool_names=set())
