"""Discovery capabilities: catalogue, grant eligibility, and execution routing (PM1.B3)."""

from __future__ import annotations

from product_factory.context.assembler import AGENT_PROFILES
from product_factory.domain.capabilities import (
    CAPABILITIES,
    CAPABILITY_TOOL_CLASSES,
    EXTERNAL_READ_TOOL_CLASSES,
)
from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.orchestration.concurrency import (
    ALWAYS_CONCURRENT_CAPABILITIES,
    partition_wave,
)
from product_factory.orchestration.coordinator import (
    _DECISION_ANALYSIS_TOOL_NAMES,
    _DISCOVERY_CAPABILITIES,
    _RESEARCH_LOOP_CAPABILITIES,
    _RETRIEVAL_LOOP_TOOL_NAMES,
)
from product_factory.planning.compiler import compile_plan
from product_factory.planning.planner import build_planner_messages
from product_factory.scheduling.scheduler import select_model
from product_factory.tools.registry import default_tool_registry

DISCOVERY_CAPABILITIES = ("domain_research", "decision_analysis")


def _task(
    task_id: str,
    capability: str,
    tool_classes: set[str],
    *,
    dependencies: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        title=task_id,
        capability=capability,  # type: ignore[arg-type]
        objective="obj",
        dependencies=dependencies or [],
        expected_output_schema="a.v1",
        required_tool_classes=tool_classes,
        acceptance_criteria=[
            AcceptanceCriterion(id="ac1", description="d", verification="evidence_check")
        ],
    )


def _plan(tasks: list[TaskSpec]) -> PlannerOutput:
    return PlannerOutput(
        objective="demo",
        tasks=tasks,
        final_artifacts=[
            FinalArtifactSpec(logical_name="dossier.md", composer_task_id=tasks[-1].id)
        ],
    )


def test_capability_catalogue_contains_discovery_capabilities() -> None:
    for capability in DISCOVERY_CAPABILITIES:
        assert capability in CAPABILITIES
        assert capability in CAPABILITY_TOOL_CLASSES


def test_discovery_tool_class_maps() -> None:
    assert CAPABILITY_TOOL_CLASSES["domain_research"] == frozenset(
        {"repository_read", "artifact_write", "web_read", "source_read", "evidence_build"}
    )
    assert CAPABILITY_TOOL_CLASSES["decision_analysis"] == frozenset(
        {"artifact_write", "evidence_build"}
    )


def test_decision_analysis_cannot_retrieve() -> None:
    permitted = CAPABILITY_TOOL_CLASSES["decision_analysis"]
    assert "web_read" not in permitted
    assert "source_read" not in permitted


def test_existing_capabilities_do_not_gain_retrieval() -> None:
    # source_read/evidence_build reach a task only through an explicit
    # required_tool_classes declaration, never through the external-read default.
    assert "source_read" not in EXTERNAL_READ_TOOL_CLASSES
    assert "evidence_build" not in EXTERNAL_READ_TOOL_CLASSES
    for capability in ("architecture", "repository_analysis", "security_review", "test_design"):
        permitted = CAPABILITY_TOOL_CLASSES[capability]
        assert "source_read" not in permitted
        assert "evidence_build" not in permitted


def test_external_read_grant_classes_exclude_discovery_classes() -> None:
    # Mirrors the coordinator's connector grant rule: grant_classes is the task's
    # declared classes plus the capability's permitted *external read* classes.
    domain_research_defaults = CAPABILITY_TOOL_CLASSES["domain_research"] & (
        EXTERNAL_READ_TOOL_CLASSES
    )
    assert domain_research_defaults == frozenset({"web_read"})
    architecture_defaults = CAPABILITY_TOOL_CLASSES["architecture"] & EXTERNAL_READ_TOOL_CLASSES
    assert "source_read" not in architecture_defaults


def test_compiler_accepts_discovery_tool_classes() -> None:
    result = compile_plan(
        _plan(
            [
                _task("T-001", "domain_research", {"source_read", "evidence_build", "web_read"}),
                _task(
                    "T-002",
                    "decision_analysis",
                    {"evidence_build", "artifact_write"},
                    dependencies=["T-001"],
                ),
            ]
        ),
        enforce_output_schemas=False,
        require_baseline_validators=False,
    )
    assert result.ok, result.errors


def test_compiler_rejects_retrieval_for_non_discovery_capability() -> None:
    result = compile_plan(
        _plan([_task("T-001", "architecture", {"source_read"})]),
        enforce_output_schemas=False,
    )
    assert not result.ok
    assert any(error.code == "tool_not_permitted" for error in result.errors)


def test_compiler_rejects_web_read_for_decision_analysis() -> None:
    result = compile_plan(
        _plan([_task("T-001", "decision_analysis", {"web_read"})]),
        enforce_output_schemas=False,
    )
    assert not result.ok
    assert any(error.code == "tool_not_permitted" for error in result.errors)


def test_planner_advertises_discovery_capabilities() -> None:
    messages = build_planner_messages(
        request_text="is this feasible?",
        workflow_type="feasibility_discovery",
        repository_summary=None,
        budget={},
    )
    payload = messages[-1].content
    for capability in DISCOVERY_CAPABILITIES:
        assert f'"{capability}"' in payload


def test_agent_profiles_cover_discovery_roles() -> None:
    assert "researcher" in AGENT_PROFILES
    assert "decision_analyst" in AGENT_PROFILES
    assert AGENT_PROFILES["researcher"] != AGENT_PROFILES["decision_analyst"]


def test_scheduler_treats_discovery_as_read_only_worker() -> None:
    for capability in DISCOVERY_CAPABILITIES:
        assert select_model(_task("T-001", capability, set())) == "fast_worker"


def test_discovery_tasks_are_always_concurrent() -> None:
    for capability in DISCOVERY_CAPABILITIES:
        assert capability in ALWAYS_CONCURRENT_CAPABILITIES
    tasks = [
        _task("A", "domain_research", set()),
        _task("B", "decision_analysis", set()),
    ]
    concurrent, serial = partition_wave(tasks)
    assert {t.id for t in concurrent} == {"A", "B"}
    assert serial == []


def test_coordinator_routes_discovery_through_draft_branch() -> None:
    for capability in DISCOVERY_CAPABILITIES:
        assert capability in _RESEARCH_LOOP_CAPABILITIES
    assert {"architecture", "requirements"} <= _RESEARCH_LOOP_CAPABILITIES


def test_only_discovery_capabilities_see_the_evidence_plane() -> None:
    # Evidence tools are offered to the research loop for discovery only, so an
    # architecture task keeps exactly its pre-PM1 tool set.
    assert set(_DISCOVERY_CAPABILITIES) == set(DISCOVERY_CAPABILITIES)
    assert "architecture" not in _DISCOVERY_CAPABILITIES
    assert "requirements" not in _DISCOVERY_CAPABILITIES


def test_retrieval_tools_trigger_the_research_loop_and_comparison_does_not() -> None:
    assert {"web_search", "fetch_source", "extract_document", "normalize_citation"} <= (
        _RETRIEVAL_LOOP_TOOL_NAMES
    )
    # decision_analysis stays one-shot: compare_options is local and needs no loop.
    assert "compare_options" not in _RETRIEVAL_LOOP_TOOL_NAMES
    assert set(_DECISION_ANALYSIS_TOOL_NAMES) == {"compare_options"}


def test_decision_analysis_grant_resolves_against_registered_tools() -> None:
    registered = {tool.name for tool in default_tool_registry().list()}
    assert registered & _DECISION_ANALYSIS_TOOL_NAMES == {"compare_options"}
