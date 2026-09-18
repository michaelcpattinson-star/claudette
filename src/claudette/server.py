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
This server exposes a corpus of texts written by women as citeable passages.
{PROVENANCE}

Two tiers may be present. The CORE is 36 works a person chose, checked and
annotated. The FULL tier, if the operator built it, adds every Project Gutenberg
text whose every author, editor and translator Wikidata records as a woman —
thousands of works, editions unreviewed, so front matter by others may remain.
Each hit says which it came from (`curated`).

Read the `status` field of every response before reading `data`:
  ok           — passages found that match most of the question's terms.
  weak         — something matched, thinly. If you use it, say the match is partial.
  no_coverage  — the corpus does not speak to this. Say that. Do not answer from
                 elsewhere; the point of this server is that the reader knows
                 whose words they are getting.
  not_found    — a specific reference did not resolve.

Cite every claim as [Author, Title §n], using the `ref` on each hit, so a reader
can verify it with read_passage. Prefer the author's own words to paraphrase.
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

    Call this before answering anything of substance. Returns ranked passages,
    each with a citation `ref` and the list of query terms it actually contains.
    Not for reading a passage you already have a ref for — use read_passage.
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
