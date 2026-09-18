"""The persona lives in one place; the skill and the subagent embed it verbatim."""

import re

from claudette import manifest_path
from claudette.persona import SYSTEM

CLAUDE = manifest_path().parent / "claude"


def _embedded(name: str) -> str:
    text = (CLAUDE / name).read_text()
    m = re.search(r"<!-- persona:begin -->\n(.*?)\n<!-- persona:end -->", text, re.S)
    assert m, f"{name}: no persona block"
    return m.group(1)


def test_skill_and_agent_embed_the_current_persona():
    assert _embedded("SKILL.md") == SYSTEM.rstrip()
    assert _embedded("agent.md") == SYSTEM.rstrip()


def test_agent_has_only_claudette_tools():
    head = (CLAUDE / "agent.md").read_text().split("---")[1]
    tools = re.search(r"^tools:\s*(.+)$", head, re.M).group(1)
    assert all(t.strip().startswith("mcp__claudette__") for t in tools.split(","))


def test_persona_bridges_to_the_present_rather_than_refusing():
    assert "you still answer" in SYSTEM
    assert "Do not add facts about the modern thing" in SYSTEM


def test_persona_has_lens_mode_with_verification():
    assert "LENS" in SYSTEM and "verify_attribution" in SYSTEM
    assert "No framework by a man" in SYSTEM
