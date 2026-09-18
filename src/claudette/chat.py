"""The chat client. The only module in the package that talks to a model.

Kept separate, behind an optional dependency group, so the corpus, index and
server can be reviewed as things that hold no key and call nothing.

The loop is manual rather than the SDK's tool runner so that every passage
the model retrieves is recorded, and the finished answer can be checked
against them: a citation that points at a passage the model never saw is
reported as unverified. That check is the difference between "Claudette
cites her sources" and "Claudette produces citation-shaped text".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from claudette.bootstrap import ensure_index
from claudette.index import Index
from claudette.persona import SYSTEM

DEFAULT_MODEL = "claude-opus-5"

TOOLS = [
    {
        "name": "search_corpus",
        "description": (
            "Find passages across the corpus that bear on a question. Call before answering anything of "
            "substance. Returns ranked passages, each with a citation ref and the query terms it contains. "
            "Older vocabulary matches better: 'sympathy' over 'empathy', 'master and men' over 'management'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The idea to look for, in a few content words."},
                "k": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8},
                "work": {"type": "string", "description": "Optional slug to restrict to one work."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_passage",
        "description": "Return one passage verbatim with its neighbours, by ref from search_corpus (e.g. 'follett-new-state§412').",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "context": {"type": "integer", "minimum": 0, "maximum": 5, "default": 1},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "verify_attribution",
        "description": "Lens mode: before naming a thinker or work from memory, check on Wikidata that she exists, is recorded as a woman, and wrote it. ok = name her; weak = name with caveat; not_found = do not.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "work": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_works",
        "description": "List every work in the corpus with author, title, year, why it is included, and slug. Not a search.",
        "input_schema": {
            "type": "object",
            "properties": {"shelf": {"type": "string", "enum": ["thought", "fiction"]}},
        },
    },
]

_CITE = re.compile(r"\[([^\[\]]*?)§\s*(\d+)\]")


@dataclass
class Turn:
    """What happened while answering one question."""

    answer: str = ""
    retrieved: dict[str, dict] = field(default_factory=dict)  # ref -> hit
    tool_calls: list[dict] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    stop_reason: str | None = None


def _run_tool(index: Index, name: str, args: dict, turn: Turn) -> str:
    if name == "search_corpus":
        resp = index.search(args["query"], k=int(args.get("k", 8)), slug=args.get("work"))
        if resp.data:
            for hit in resp.data:
                turn.retrieved[hit["ref"]] = hit
    elif name == "read_passage":
        resp = index.read(args["ref"], context=int(args.get("context", 1)))
        if resp.data:
            for p in resp.data["passages"]:
                turn.retrieved.setdefault(p["ref"], {**p, "author": resp.data["author"], "title": resp.data["title"]})
    elif name == "verify_attribution":
        from claudette.attribution import verify

        resp = verify(args["name"], args.get("work"))
    elif name == "list_works":
        resp = index.works(args.get("shelf"))
        return json.dumps({"status": "ok", "data": resp})
    else:
        return json.dumps({"status": "not_found", "limitations": [f"unknown tool {name}"]})
    turn.statuses.append(resp.status)
    return resp.model_dump_json()


def check_citations(answer: str, retrieved: dict[str, dict]) -> list[str]:
    """Citations in the answer that do not point at a passage retrieved this turn.

    A citation is [Author, Title §n]. It verifies if some retrieved passage has
    ordinal n and an author whose surname appears in the bracket.
    """
    bad = []
    for m in _CITE.finditer(answer):
        label, n = m.group(1).lower(), int(m.group(2))
        found = any(
            h.get("ordinal", int(ref.split("§")[1])) == n and h["author"].split()[-1].lower() in label
            for ref, h in retrieved.items()
        )
        if not found:
            bad.append(m.group(0))
    return bad


def ask(question: str, *, history: list | None = None, model: str = DEFAULT_MODEL, index: Index | None = None, on_tool=None, client=None) -> Turn:
    """Answer one question. `history` (mutated) carries a multi-turn conversation.

    `client` is injectable so the loop can be tested without a network.
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    index = index or Index(ensure_index())
    messages = history if history is not None else []
    messages.append({"role": "user", "content": question})
    turn = Turn()

    while True:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        messages.append({"role": "assistant", "content": response.content})
        turn.stop_reason = response.stop_reason

        if response.stop_reason == "refusal":
            turn.answer = "(The model declined to answer this request.)"
            break
        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            turn.answer = "".join(b.text for b in response.content if b.type == "text")
            break

        results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input)
            turn.tool_calls.append({"name": tu.name, "args": args})
            if on_tool:
                on_tool(tu.name, args)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _run_tool(index, tu.name, args, turn)})
        messages.append({"role": "user", "content": results})

    turn.unverified_citations = check_citations(turn.answer, turn.retrieved)
    return turn
