"""`claudette` — build the corpus, search it, talk to it, serve it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claudette import __version__, db_path, manifest_path, texts_dir
from claudette.manifest import load_manifest


def _paths() -> tuple[Path, Path, Path]:
    return manifest_path(), texts_dir(), db_path()


def _index():
    """Read commands bootstrap the index like the server does, so a fresh install just works."""
    from claudette.bootstrap import ensure_index
    from claudette.index import Index

    return Index(ensure_index())


def cmd_fetch(a: argparse.Namespace) -> int:
    from claudette.fetch import fetch_all

    manifest_p, texts, _ = _paths()
    m = load_manifest(manifest_p)
    print(f"{m.name}: {len(m.works)} works by {len(m.authors())} authors")
    fetched = fetch_all(m, texts, force=a.force)
    print(f"fetched {len(fetched)}, cleaned {len(m.works)} → {texts}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    from claudette.fetch import verify_all

    manifest_p, texts, _ = _paths()
    m = load_manifest(manifest_p)
    problems = verify_all(m, texts, show=a.show)
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"\nall {len(m.works)} headers match the manifest")
    return 0


def cmd_index(a: argparse.Namespace) -> int:
    from claudette.fetch import clean_path
    from claudette.index import build

    manifest_p, texts, db = _paths()
    m = load_manifest(manifest_p)

    def text_for(w):
        p = clean_path(texts, w)
        return p.read_text(encoding="utf-8") if p.exists() else None

    counts = build(m, text_for, db, tier="core")
    print(f"{sum(counts.values())} passages from {len(counts)} works → {db}")
    return 0


def cmd_expand(a: argparse.Namespace) -> int:
    from claudette.expand import expand

    langs = set(a.languages.split(",")) if a.languages else None
    out = expand(languages=langs, limit=a.limit, skip_fetch=a.skip_fetch)
    print(f"full tier at {out}; the server will use it from now on. Delete it to go back to the core.")
    return 0


def cmd_catalog(a: argparse.Namespace) -> int:
    """Maintainers only: regenerate authors.csv and works.full.csv from Wikidata and Gutenberg."""
    import urllib.parse
    import urllib.request
    from datetime import date

    from claudette import catalog_path
    from claudette.catalog import WIKIDATA_QUERY, build_catalog, write_catalog
    from claudette.fetch import USER_AGENT

    data = catalog_path().parent
    authors = data / "authors.csv"
    print("wikidata: querying women with Gutenberg author IDs")
    req = urllib.request.Request(
        "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY}),
        headers={"Accept": "text/csv", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        authors.write_bytes(r.read())
    rdf = Path(a.rdf) if a.rdf else texts_dir().parent / "rdf-files.tar.bz2"
    if not rdf.exists():
        print("gutenberg: downloading RDF metadata dump (~120 MB)")
        req = urllib.request.Request("https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=900) as r, open(rdf, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
    works = build_catalog(rdf, authors)
    write_catalog(works, catalog_path(), generated=f"{date.today().isoformat()} from Wikidata + Gutenberg RDF dump")
    print(f"wrote {authors} and {catalog_path()}")
    return 0


def cmd_search(a: argparse.Namespace) -> int:
    resp = _index().search(a.query, k=a.k, slug=a.work)
    print(f"status: {resp.status}")
    for lim in resp.limitations:
        print(f"  ! {lim}")
    for h in resp.data or []:
        tag = "" if h["curated"] else "  [full tier, unreviewed]"
        print(f"\n[{h['author']}, {h['title']} §{h['ordinal']}]  ref={h['ref']}  matched={h['matched_terms']}{tag}")
        print("  " + h["text"][:600].replace("\n", "\n  ") + ("…" if len(h["text"]) > 600 else ""))
    return 0


def cmd_read(a: argparse.Namespace) -> int:
    resp = _index().read(a.ref, context=a.context)
    print(f"status: {resp.status}")
    for lim in resp.limitations:
        print(f"  ! {lim}")
    if resp.data:
        print(f"{resp.data['author']}, {resp.data['title']} ({resp.data['year']})")
        for p in resp.data["passages"]:
            print(f"\n§{p['ordinal']}\n{p['text']}")
    return 0


def cmd_works(a: argparse.Namespace) -> int:
    idx = _index()
    st = idx.stats()
    print(f"{st['tier']} tier: {st['works']} works, {st['authors']} authors, {st['passages']} passages, {st['languages']} languages\n")
    for w in idx.works(a.shelf, author=a.author, limit=a.limit):
        yr = w["year"] or "n.d."
        flag = " " if w["curated"] else "~"
        print(f"{flag}{yr!s:5s} {w['author'][:28]:28s} {w['title'][:50]:50s}  {w['slug']}  ({w['passages']})")
    return 0


def cmd_authors(a: argparse.Namespace) -> int:
    for r in _index().authors(a.query, limit=a.limit):
        flag = "*" if r["curated"] else " "
        print(f"{flag} {r['works']:4d} works {r['passages']:7d} passages  {r['author']}")
    return 0


def _print_turn(turn, verbose: bool) -> None:
    print(turn.answer)
    if verbose:
        print(f"\n— {len(turn.tool_calls)} searches, {len(turn.retrieved)} passages retrieved, statuses {turn.statuses}")
    if turn.unverified_citations:
        print("\n! citations not matching any passage retrieved this turn (treat as unverified):")
        for c in turn.unverified_citations:
            print("    " + c)


def cmd_ask(a: argparse.Namespace) -> int:
    from claudette.chat import ask

    on_tool = (lambda n, args: print(f"  ↳ {n}({args.get('query') or args.get('ref') or ''})", file=sys.stderr)) if a.verbose else None
    turn = ask(a.question, model=a.model, on_tool=on_tool)
    _print_turn(turn, a.verbose)
    return 0


def cmd_chat(a: argparse.Namespace) -> int:
    from claudette.chat import ask

    print("Claudette. Ask; she answers from the corpus and cites. Ctrl-D to leave.")
    history: list = []
    on_tool = (lambda n, args: print(f"  ↳ {n}({args.get('query') or args.get('ref') or ''})", file=sys.stderr)) if a.verbose else None
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        turn = ask(q, history=history, model=a.model, on_tool=on_tool)
        print("\nclaudette> ", end="")
        _print_turn(turn, a.verbose)


def cmd_install(a: argparse.Namespace) -> int:
    """Put the skill and the subagent where Claude Code looks for them."""
    import shutil

    from claudette import manifest_path

    src = manifest_path().parent / "claude"
    root = Path(a.into).expanduser()
    targets = {
        src / "SKILL.md": root / "skills" / "claudette" / "SKILL.md",
        src / "agent.md": root / "agents" / "claudette.md",
    }
    for s_, d in targets.items():
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(s_, d)
        print(f"  {d}")
    print("\nInstalled. In a new Claude Code session: type /claudette, or say 'ask Claudette …' to use the subagent.")
    print("Both need the connector:  claude mcp add --scope user claudette -- uvx --from git+https://github.com/michaelcpattinson-star/claudette claudette-mcp")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    from claudette.server import main

    main(transport="streamable-http" if a.http else "stdio", host=a.host, port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="claudette", description="An assistant that answers only from women-authored texts.")
    p.add_argument("--version", action="version", version=f"claudette {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="download the corpus from Project Gutenberg and strip boilerplate")
    s.add_argument("--force", action="store_true", help="re-download even if present")
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("verify", help="check each downloaded text's header against the manifest")
    s.add_argument("--show", type=int, default=0, metavar="N", help="print the first N lines of each cleaned text")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("index", help="build the FTS5 index from fetched texts")
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("search", help="search the index directly (no model)")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--work", help="restrict to a slug")
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("read", help="print a passage by ref, e.g. follett-new-state§412")
    s.add_argument("ref")
    s.add_argument("--context", type=int, default=1)
    s.set_defaults(fn=cmd_read)

    s = sub.add_parser("works", help="list the works in the index (~ marks the unreviewed full tier)")
    s.add_argument("--shelf", choices=["thought", "fiction", "uncurated"])
    s.add_argument("--author", help="substring of an author's name")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn=cmd_works)

    s = sub.add_parser("authors", help="who is in the index, and how much (* = curated)")
    s.add_argument("query", nargs="?")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_authors)

    s = sub.add_parser("expand", help="build the FULL tier: every catalogued Gutenberg text by women (slow, GBs, resumable)")
    s.add_argument("--languages", help="comma-separated ISO codes, e.g. en,fr. Default: all")
    s.add_argument("--limit", type=int, help="only the first N catalogued works (for a trial run)")
    s.add_argument("--skip-fetch", action="store_true", help="index what is already on disk without contacting the mirror")
    s.set_defaults(fn=cmd_expand)

    s = sub.add_parser("catalog", help="(maintainers) regenerate data/authors.csv and data/works.full.csv")
    s.add_argument("--rdf", help="path to an already-downloaded rdf-files.tar.bz2")
    s.set_defaults(fn=cmd_catalog)

    s = sub.add_parser("ask", help="ask one question (needs the `chat` dependency group and Anthropic credentials)")
    s.add_argument("question")
    s.add_argument("--model", default="claude-opus-5")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("chat", help="multi-turn conversation")
    s.add_argument("--model", default="claude-opus-5")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("install", help="install the /claudette skill and the claudette subagent into Claude Code")
    s.add_argument("--into", default="~/.claude", help="Claude Code config root (default ~/.claude; use a project's .claude for project scope)")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("serve", help="run the MCP server (stdio by default; --http to host it)")
    s.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp instead of stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
