"""The persona lives in one place; the skill and the subagent embed it verbatim."""

import re

from claudette import manifest_path
from claudette.persona import FORMATION, PROMPT, SYSTEM

CLAUDE = manifest_path().parent / "claude"


def _embedded(name: str) -> str:
    text = (CLAUDE / name).read_text()
    m = re.search(r"<!-- persona:begin -->\n(.*?)\n<!-- persona:end -->", text, re.S)
    assert m, f"{name}: no persona block"
    return m.group(1)


def test_skill_and_agent_embed_the_current_persona_and_formation():
    assert _embedded("SKILL.md") == PROMPT.rstrip()
    assert _embedded("agent.md") == PROMPT.rstrip()
    assert FORMATION in PROMPT


def test_formation_grounds_every_conviction_in_refs_and_verified_names():
    import re

    sections = [b for b in FORMATION.split("\n## ")[1:] if not b.startswith("How I use this")]
    assert len(sections) >= 15
    for sec in sections:
        assert "Grounds:" in sec, sec[:60]
        assert re.search(r"[a-z0-9-]+§\d+", sec), sec[:60]
        assert "Carried forward:" in sec, sec[:60]


def test_persona_bans_library_card_talk():
    for phrase in ("on my shelves", "public domain cut", "I hold nothing"):
        assert phrase in SYSTEM  # named so she knows what not to say
    assert "Answer from your formation first" in SYSTEM


def test_agent_has_only_claudette_tools():
    head = (CLAUDE / "agent.md").read_text().split("---")[1]
    tools = re.search(r"^tools:\s*(.+)$", head, re.M).group(1)
    assert all(t.strip().startswith("mcp__claudette__") for t in tools.split(","))


def test_persona_bridges_to_the_present_rather_than_refusing():
    assert "You don't." in SYSTEM  # the corpus stops in the 1920s; she doesn't
    assert "come from the user, not from you" in " ".join(SYSTEM.split())  # wraps across lines


def test_persona_answers_like_claude_not_like_a_librarian():
    assert "Lead with the answer" in SYSTEM
    assert "No citations in the body" in SYSTEM and "Sources:" in SYSTEM
    assert "Never talk about your library" in SYSTEM


def test_persona_has_lens_mode_with_verification():
    assert "Lens" in SYSTEM and "verify_attribution" in SYSTEM
    assert "never as the lens" in SYSTEM
