"""The server's tool surface, and the citation check that keeps the client honest."""

import asyncio

from claudette.chat import TOOLS, check_citations
from claudette.server import mcp


def test_tool_surface_is_small_and_non_overlapping():
    tools = asyncio.run(mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names == ["corpus_provenance", "list_authors", "list_works", "read_passage", "search_corpus", "verify_attribution"]
    assert len(tools) <= 8
    for t in tools:
        assert t.description and ("Not" in t.description or "Use" in t.description), t.name


def test_chat_tools_mirror_server_tools_that_need_no_bootstrap():
    server_names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {t["name"] for t in TOOLS} <= server_names


def test_prompt_is_registered():
    prompts = asyncio.run(mcp.list_prompts())
    assert [p.name for p in prompts] == ["claudette"]


def test_citation_check_accepts_only_retrieved_passages():
    retrieved = {"follett-new-state§412": {"author": "Mary Parker Follett", "ordinal": 412}}
    answer = (
        "Follett says so [Mary Parker Follett, The New State §412]. "
        "She also says [Follett, The New State §413]. "
        "And Gaskell [Elizabeth Gaskell, North and South §412]."
    )
    bad = check_citations(answer, retrieved)
    assert bad == ["[Follett, The New State §413]", "[Elizabeth Gaskell, North and South §412]"]


def test_citation_check_tolerates_hash_and_spacing():
    retrieved = {"x§7": {"author": "Ada Fixture", "ordinal": 7}}
    assert check_citations("see [Fixture, On the Committee § 7]", retrieved) == []


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


class _FakeClient:
    """Scripted model: searches, then answers with one good and one invented citation."""

    def __init__(self):
        self.calls = []
        self.beta = self
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            assert kw["messages"][-1]["content"] == "Who holds power over whom?"
            assert kw["tools"] and kw["system"][0]["text"].startswith("You are Claudette")
            return _Resp([_Block(type="tool_use", id="t1", name="search_corpus", input={"query": "power over consent"})], "tool_use")
        # second call: the tool result must be there, with a status the model can read
        result = kw["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "t1"
        assert '"status":"ok"' in result["content"]
        self.hit_ordinal = int(result["content"].split('"ordinal":')[1].split(",")[0])
        return _Resp(
            [_Block(type="text", text=f"She says so [Ada Fixture, On the Committee §{self.hit_ordinal}] and also [Fixture, On the Committee §999].")],
            "end_turn",
        )


def test_ask_loop_plumbs_tool_results_and_flags_invented_citations(index):
    from claudette.chat import ask

    fake = _FakeClient()
    history: list = []
    turn = ask("Who holds power over whom?", index=index, client=fake, history=history)
    assert len(fake.calls) == 2
    assert turn.tool_calls == [{"name": "search_corpus", "args": {"query": "power over consent"}}]
    assert turn.statuses == ["ok"]
    assert f"fixture-committee§{fake.hit_ordinal}" in turn.retrieved
    assert turn.unverified_citations == ["[Fixture, On the Committee §999]"]
    assert turn.stop_reason == "end_turn"
    # history is usable for a follow-up turn: user, assistant(tool_use), user(tool_result), assistant(text)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_server_instructions_carry_the_voice():
    from claudette.server import INSTRUCTIONS

    for phrase in ("answering AS Claudette", "NO citations in the body", "Sources:", "delegate", "verify_attribution", "# What I think", "Never talk about your library"):
        assert phrase in INSTRUCTIONS, phrase
