"""The MCP server.

Four tools, no overlap: search, read, list, provenance. Tool descriptions are
interface, not documentation — they are the only thing a model sees when
choosing — so each says what it does and when not to use it.

The server holds no key and makes no network call. Everything it returns is
a passage from the index, stamped by the envelope with where it came from.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from claudette import __version__, manifest_path
from claudette.bootstrap import ensure_index
from claudette.envelope import PROVENANCE, ToolResponse
from claudette.index import Index
from claudette.manifest import load_manifest
from claudette.persona import SYSTEM

INSTRUCTIONS = f"""\
CLAUDETTE. These tools are not a research API to be reported on. When you use
them, you are answering AS Claudette, and this is how she talks:

- You answer the way Claude answers — directly, in your own voice, with a view
  in the first sentence — from a formation in women's writing. The corpus is
  where you learned what you think, not your subject. Never "the corpus holds",
  "within these texts", "Claudette is a retrieval tool". Never inventory the
  sources under headings. Structure by the question.
- Names in the prose the way a well-read person uses them — "Marot's word for
  it was tricks" — and short quotations where hers beat yours.
- NO citations in the body. End with one line, "Sources:", listing every
  passage you drew on as [Author, Title §n] using each hit's `ref`, plus any
  attribution from memory marked "from memory — check".
- Reach the present. The texts stop in the 1920s; you don't. Carry the thread
  forward through women thinkers of any era — call verify_attribution before
  naming one; paraphrase, never quote from memory. Men may appear as facts of
  history ("the management writers of the 1950s rediscovered her"), never as
  the lens. Facts about the modern thing itself come from the user.
- No meta about tools, metadata or process. If something is missing, one line
  and move on. Three or four paragraphs is the usual length.

If a `claudette` subagent is available, delegate the whole question to it and
relay its answer verbatim rather than calling these tools yourself.

Reading results: `status` before `data`. ok — use it. weak — use with care and
say the match is loose if you lean on it. no_coverage — nothing there.
not_found — a ref did not resolve. `curated: false` marks unreviewed editions
from the full tier, fine to use. `year` is publication year for curated works;
for full-tier works it is absent — do not invent one.

{PROVENANCE}
"""

mcp = MCPServer(
    "claudette",
    title="Claudette — women-authored corpus",
    instructions=INSTRUCTIONS,
    version=__version__,
)

_index: Index | None = None


def index() -> Index:
    global _index
    if _index is None:
        _index = Index(ensure_index())
    return _index


@mcp.tool()
def search_corpus(
    query: Annotated[str, Field(description="The idea to look for, in a few content words. Older vocabulary matches better: 'sympathy' over 'empathy', 'master and men' over 'management'.")],
    k: Annotated[int, Field(ge=1, le=25, description="How many passages to return.")] = 8,
    work: Annotated[str | None, Field(description="Restrict to one work by slug (see list_works). Leave unset to search everything.")] = None,
    language: Annotated[str | None, Field(description="ISO 639-1 code to restrict to, e.g. 'en', 'fr'. Unset searches all.")] = None,
    curated_only: Annotated[bool, Field(description="Search only the reviewed core, not the generated full tier.")] = False,
) -> ToolResponse:
    """Find passages across the corpus that bear on a question.

    Call this before answering anything of substance, then answer AS Claudette
    (see the server instructions: your own voice, view first, no citations in
    the body, a Sources: line at the end). Returns ranked passages, each with a
    citation `ref` and the query terms it actually contains. Not for reading a
    passage you already have a ref for — use read_passage.
    """
    return index().search(query, k=k, slug=work, language=language, curated_only=curated_only)


@mcp.tool()
def read_passage(
    ref: Annotated[str, Field(description="A citation ref from search_corpus, e.g. 'follett-new-state§412'.")],
    context: Annotated[int, Field(ge=0, le=5, description="How many neighbouring passages to include either side.")] = 1,
) -> ToolResponse:
    """Return one passage verbatim, with its neighbours, for quoting or checking a citation.

    Use after search_corpus when you need the surrounding text. Not for finding
    passages — it takes a ref, not a question.
    """
    return index().read(ref, context=context)


@mcp.tool()
def list_works(
    shelf: Annotated[str | None, Field(description="'thought', 'fiction' (both curated), or 'uncurated' for the full tier. Unset lists curated first.")] = None,
    author: Annotated[str | None, Field(description="Substring of an author's name, to see what the corpus holds by her.")] = None,
    limit: Annotated[int, Field(ge=1, le=500, description="Maximum rows. The full tier has thousands; filter by author rather than paging.")] = 100,
) -> ToolResponse:
    """List works in the corpus: author, title, year, why it is included, source, and slug.

    Use to tell the user what the corpus holds, or to pick a slug for a
    restricted search. Not a search — it returns no passages. For the full
    tier, filter by author; use list_authors to find her name first.
    """
    rows = index().works(shelf, author=author, limit=limit)
    lim = [f"Showing {limit}; filter by author for more."] if len(rows) == limit else []
    return ToolResponse.ok(rows, limitations=lim)


@mcp.tool()
def list_authors(
    query: Annotated[str | None, Field(description="Substring of a name. Unset lists the most represented authors.")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
) -> ToolResponse:
    """Find which women are in the corpus and how much of each: name, works, passages, curated.

    Use when the user asks 'is X in there?' or 'who do you have on Y?'. Not a
    passage search — pair with search_corpus once you know the name.
    """
    return ToolResponse.ok(index().authors(query, limit=limit))


@mcp.tool()
def corpus_provenance() -> ToolResponse:
    """State what this corpus is, where it came from, how it was curated, and its known limits.

    Use when the user asks what Claudette is, whose words these are, or how
    the guarantee is enforced.
    """
    m = load_manifest(manifest_path())
    return ToolResponse.ok(
        {
            "name": m.name,
            "source": m.source,
            "principle": m.principle,
            "stats": index().stats(),
            "authors": m.authors(),
            "how_enforced": (
                "Core: the index is built only from works listed in the packaged manifest (corpus/manifest.toml), each with a named "
                "woman author and a reviewed edition. There is no classifier; a reviewer can read the whole list. "
                "Full tier: every Gutenberg text whose every creator, editor and translator has a Wikidata record with "
                "sex or gender = female (data/authors.csv carries the Wikidata ID of each). Generated, reproducible, unreviewed."
            ),
        },
        limitations=[
            "The prose of an answer is generated by a general model; only its sources are constrained. "
            "The guarantee is about provenance of evidence, not about the training data of the model.",
            "Public-domain scope means the corpus mostly ends in the 1920s and is heavily English-language.",
            "Curated editions have had front matter by others trimmed; full-tier editions have not been checked and may carry a preface by another hand.",
        ],
    )


@mcp.tool()
def verify_attribution(
    name: Annotated[str, Field(description="A thinker or writer you intend to name from memory, e.g. 'Elinor Ostrom'.")],
    work: Annotated[str | None, Field(description="The work you intend to attribute to her, e.g. 'Governing the Commons'. Optional but strongly encouraged.")] = None,
) -> ToolResponse:
    """Check against Wikidata that a person exists, is recorded as a woman, and wrote the named work.

    Lens mode only: call this BEFORE naming any author or work from memory
    rather than from the corpus. `ok` means name her. `weak` means name her
    with the stated caveat. `not_found` means do not. Not for authors already
    in the corpus — search_corpus is their check. The one network call this
    server makes; it goes only to Wikidata.
    """
    from claudette.attribution import verify

    return verify(name, work)


@mcp.prompt(name="claudette", description="Claudette's standing instructions: answer only from the corpus, cite everything, say when it does not speak to the question.")
def claudette_prompt() -> str:
    return SYSTEM


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    """stdio for a local connector; streamable-http to host it for others.

    The index is bootstrapped before the transport opens so that the first
    tool call is fast and any download progress goes to stderr, not the wire.
    """
    ensure_index()
    if transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.run("streamable-http", host=host, port=port, stateless_http=True, json_response=True)


if __name__ == "__main__":
    main()
