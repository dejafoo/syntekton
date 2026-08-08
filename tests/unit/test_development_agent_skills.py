"""Structural checks for repository development-agent skills."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
SKILLS_ROOT = ROOT / ".cursor" / "skills"


def _routed_skill_names() -> set[str]:
    rows: list[str] = []
    in_table = False
    for line in (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines():
        if line == "| Change | Required skill |":
            in_table = True
            continue
        if in_table and not line.startswith("|"):
            break
        if in_table:
            rows.append(line)
    return set(re.findall(r"`([a-z0-9-]+)`", "\n".join(rows)))


def test_every_project_skill_is_routed_and_structurally_complete() -> None:
    skill_dirs = {path.name for path in SKILLS_ROOT.iterdir() if path.is_dir()}
    assert _routed_skill_names() == skill_dirs

    for name in sorted(skill_dirs):
        skill_file = SKILLS_ROOT / name / "SKILL.md"
        agent_file = SKILLS_ROOT / name / "agents" / "openai.yaml"
        assert skill_file.is_file(), name
        assert agent_file.is_file(), name

        skill_text = skill_file.read_text(encoding="utf-8")
        agent_text = agent_file.read_text(encoding="utf-8")
        frontmatter = skill_text.split("---", maxsplit=2)[1]

        assert re.search(rf"^name: {re.escape(name)}$", frontmatter, re.MULTILINE)
        assert re.search(r"^description: \S.+$", frontmatter, re.MULTILINE)
        display_name = re.search(r'^  display_name: "([^"]+)"$', agent_text, re.MULTILINE)
        short_description = re.search(r'^  short_description: "([^"]+)"$', agent_text, re.MULTILINE)
        default_prompt = re.search(r'^  default_prompt: "([^"]+)"$', agent_text, re.MULTILINE)
        assert display_name is not None
        assert short_description is not None
        assert 25 <= len(short_description.group(1)) <= 64
        assert default_prompt is not None
        assert f"${name}" in default_prompt.group(1)
        assert "TODO" not in skill_text
        assert "TODO" not in agent_text
