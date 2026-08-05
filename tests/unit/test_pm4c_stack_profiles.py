"""PM4.C deterministic repository stack-profile tests."""

from __future__ import annotations

from pathlib import Path

from product_factory.context.assembler import assemble_context
from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.planning.compiler import compile_plan
from product_factory.repository.stack_profile import discover_stack_profile
from product_factory.skills.profiles import ProfileRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _task() -> TaskSpec:
    return TaskSpec(
        id="T-profile",
        title="compose",
        capability="composition",
        objective="compose result",
        expected_output_schema="result.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="ac", description="valid", verification="static_rule")
        ],
    )


def test_sample_api_profile_is_stable_and_compact() -> None:
    kwargs = {
        "registered_command_ids": [
            "python_typecheck",
            "javascript_tests",
            "python_tests",
        ]
    }
    first = discover_stack_profile(FIXTURES / "sample_api", **kwargs)
    second = discover_stack_profile(FIXTURES / "sample_api", **kwargs)

    assert first == second
    assert first.digest == second.digest
    assert first.model_dump(mode="json") == {
        "id": "sample-api",
        "version": "1.0.0",
        "kind": "stack",
        "status": "known",
        "languages": [
            {
                "language": "python",
                "runtime": ">=3.13",
                "frameworks": [],
                "location_globs": ["src/**/*.py", "tests/**/*.py"],
            }
        ],
        "registered_command_ids": ["python_tests", "python_typecheck"],
        "source_files": ["pyproject.toml"],
        "limitations": [],
    }


def test_javascript_fixture_uses_declared_runtime_and_dependencies() -> None:
    profile = discover_stack_profile(
        FIXTURES / "sample_web",
        registered_command_ids=["python_tests", "js_tests", "javascript_lint"],
    )

    assert profile.status == "known"
    assert profile.registered_command_ids == ["javascript_lint", "js_tests"]
    assert profile.languages[0].language == "typescript"
    assert profile.languages[0].runtime == ">=20"
    assert [framework.name for framework in profile.languages[0].frameworks] == ["react"]
    assert profile.languages[0].location_globs == ["src/**/*.{ts,tsx}"]


def test_unknown_and_ambiguous_trees_fail_closed(tmp_path: Path) -> None:
    unknown = discover_stack_profile(tmp_path)
    assert unknown.status == "unknown"
    assert unknown.languages == []
    assert unknown.registered_command_ids == []

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "hybrid"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"name":"hybrid","engines":{"node":">=20"}}\n',
        encoding="utf-8",
    )
    ambiguous = discover_stack_profile(
        tmp_path,
        registered_command_ids=["python_tests", "javascript_tests"],
    )
    assert ambiguous.status == "limited"
    assert [language.language for language in ambiguous.languages] == [
        "python",
        "javascript",
    ]
    assert "Multiple fixture-language stacks require explicit selection" in (ambiguous.limitations)


def test_profile_registry_round_trips_yaml_and_digest(tmp_path: Path) -> None:
    profile = discover_stack_profile(
        FIXTURES / "sample_api",
        registered_command_ids=["python_tests"],
    )
    path = ProfileRegistry().store(tmp_path / "profiles", profile)
    loaded = ProfileRegistry.load(tmp_path / "profiles")

    assert path == tmp_path / "profiles" / "stack" / "sample-api.yaml"
    assert loaded.get("sample-api") == profile
    assert loaded.digests() == {f"stack:{profile.id}": profile.digest}


def test_profile_digest_slots_are_stable_in_context_and_compiler() -> None:
    digests = {"stack:sample-api": "abc123"}
    task = _task()
    context = assemble_context(
        task=task,
        model_profile="coding_worker",
        agent_profile="composer",
        skills=[],
        tool_definitions=[],
        package_id="profile-package",
        profile_digests=digests,
    )
    proposal = PlannerOutput(
        objective="profile-aware compile",
        tasks=[task],
        final_artifacts=[FinalArtifactSpec(logical_name="result.json", composer_task_id=task.id)],
    )
    compiled = compile_plan(proposal, profile_digests=digests)

    assert context.manifest.selected_profile_digests == digests
    assert compiled.ok
    assert compiled.plan is not None
    assert compiled.plan.profile_digests == digests
