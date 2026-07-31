"""PM1.C discovery method skills and G2 fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from product_factory.orchestration.skill_grants import enforce_skill_grants
from product_factory.domain.errors import SkillGrantViolation
from product_factory.skills.registry import SkillRegistry

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "skills"
FIXTURES_ROOT = ROOT / "tests" / "fixtures" / "discovery"

EVIDENCE_SKILL_ID = "discovery.evidence-assessment"
OPTION_SKILL_ID = "discovery.option-framing"

ESCALATION_OUTCOMES = frozenset(
    {"unknown", "insufficient_evidence", "needs_expert_review", "blocked"}
)


@pytest.fixture(scope="module")
def registry() -> SkillRegistry:
    return SkillRegistry.load(SKILLS_ROOT)


def _load_fixtures() -> list[dict]:
    fixtures: list[dict] = []
    for path in sorted(FIXTURES_ROOT.glob("g2_*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data["_path"] = str(path.relative_to(ROOT))
        fixtures.append(data)
    return fixtures


def test_discovery_skills_load_with_pm0_contract(registry: SkillRegistry) -> None:
    evidence = registry.get(EVIDENCE_SKILL_ID)
    option = registry.get(OPTION_SKILL_ID)
    assert evidence is not None
    assert option is not None

    for skill in (evidence, option):
        assert skill.manifest.owner == "product-factory"
        assert skill.manifest.max_prompt_chars == 12000
        assert "source" in skill.manifest.profile_slots
        assert len(skill.package_digest) == 64
        assert skill.content.strip()


def test_evidence_assessment_capabilities_and_prohibitions(registry: SkillRegistry) -> None:
    skill = registry.get(EVIDENCE_SKILL_ID)
    assert skill is not None
    assert skill.manifest.capabilities == ["domain_research", "independent_review"]
    assert skill.manifest.prohibited_tools == ["repository_write"]
    assert not skill.manifest.required_tools


def test_option_framing_capabilities(registry: SkillRegistry) -> None:
    skill = registry.get(OPTION_SKILL_ID)
    assert skill is not None
    assert skill.manifest.capabilities == ["decision_analysis", "composition"]
    assert not skill.manifest.required_tools


@pytest.mark.parametrize(
    "capability,skill_id",
    [
        ("domain_research", EVIDENCE_SKILL_ID),
        ("independent_review", EVIDENCE_SKILL_ID),
        ("decision_analysis", OPTION_SKILL_ID),
        ("composition", OPTION_SKILL_ID),
    ],
)
def test_match_selects_discovery_skills(
    registry: SkillRegistry, capability: str, skill_id: str
) -> None:
    matched = registry.match(capability=capability)
    ids = {s.manifest.id for s in matched}
    assert skill_id in ids


def test_evidence_guidance_covers_plan_rules(registry: SkillRegistry) -> None:
    skill = registry.get(EVIDENCE_SKILL_ID)
    assert skill is not None
    text = skill.content.lower()
    for needle in (
        "observation",
        "inference",
        "primary",
        "conflict",
        "stale",
        "escalate",
        "unknown",
        "needs_expert_review",
    ):
        assert needle in text, f"missing guidance term: {needle}"


def test_option_guidance_covers_plan_rules(registry: SkillRegistry) -> None:
    skill = registry.get(OPTION_SKILL_ID)
    assert skill is not None
    text = skill.content.lower()
    for needle in (
        "rubric",
        "before",
        "unknown",
        "reversibility",
        "blocker",
    ):
        assert needle in text, f"missing guidance term: {needle}"


def test_skills_do_not_widen_write_authority(registry: SkillRegistry) -> None:
    evidence = registry.get(EVIDENCE_SKILL_ID)
    option = registry.get(OPTION_SKILL_ID)
    assert evidence is not None and option is not None

    # Read-only / artifact-style grants are fine; neither skill requires write.
    enforce_skill_grants(
        skills=[evidence, option],
        granted_tool_names={"read_file", "list_files", "search_text", "write_artifact"},
    )

    with pytest.raises(SkillGrantViolation):
        enforce_skill_grants(
            skills=[evidence],
            granted_tool_names={"read_file", "create_file", "apply_patch"},
        )


def test_g2_fixtures_cover_required_scenarios() -> None:
    fixtures = _load_fixtures()
    scenarios = {f["scenario"] for f in fixtures}
    assert {
        "incomplete",
        "contradictory",
        "stale",
        "injection",
        "needs_expert_review",
    }.issubset(scenarios)
    assert len(fixtures) >= 5


@pytest.mark.parametrize("fixture", _load_fixtures(), ids=lambda f: f["id"])
def test_g2_fixture_expected_outcomes_are_safe(fixture: dict) -> None:
    assert fixture["skill"] in {EVIDENCE_SKILL_ID, OPTION_SKILL_ID}
    assert fixture["expected_outcome"] in ESCALATION_OUTCOMES
    if fixture["scenario"] == "injection":
        assert "must_not" in fixture
        joined = " ".join(fixture["must_not"]).lower()
        assert "repository_write" in joined or "grant" in joined
    if fixture["scenario"] == "needs_expert_review":
        assert fixture["expected_outcome"] == "needs_expert_review"
    if fixture["id"] == "g2-option-unknown-cells":
        assert fixture["expected_unknown_cells"]
        assert "rubric" in fixture


def test_injection_fixture_does_not_override_skill_authority(
    registry: SkillRegistry,
) -> None:
    skill = registry.get(EVIDENCE_SKILL_ID)
    assert skill is not None
    fixture = next(f for f in _load_fixtures() if f["id"] == "g2-injection-in-source")
    excerpt = fixture["evidence"][0]["excerpt"].lower()
    assert "grant repository_write" in excerpt
    # Skill guidance must reject injection-as-authority; grants stay enforced.
    assert "ignore" in skill.content.lower() or "injection" in skill.content.lower()
    assert skill.manifest.prohibited_tools == ["repository_write"]
    with pytest.raises(SkillGrantViolation):
        enforce_skill_grants(
            skills=[skill],
            granted_tool_names={"create_file"},
        )
