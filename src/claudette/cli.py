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
    from claudette.index import build

    manifest_p, texts, db = _paths()
    m = load_manifest(manifest_p)
    counts = build(m, texts, db)
    print(f"{sum(counts.values())} passages from {len(counts)} works → {db}")
    return 0


def cmd_search(a: argparse.Namespace) -> int:
    resp = _index().search(a.query, k=a.k, slug=a.work)
    print(f"status: {resp.status}")
    for lim in resp.limitations:
        print(f"  ! {lim}")
    for h in resp.data or []:
        print(f"\n[{h['author']}, {h['title']} §{h['ordinal']}]  ref={h['ref']}  matched={h['matched_terms']}")
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
    for w in _index().works(a.shelf):
        print(f"{w['year']}  {w['author']:28s} {w['title'][:50]:50s}  {w['slug']}  ({w['passages']} passages)")
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

    s = sub.add_parser("works", help="list the works in the index")
    s.add_argument("--shelf", choices=["thought", "fiction"])
    s.set_defaults(fn=cmd_works)

    s = sub.add_parser("ask", help="ask one question (needs the `chat` dependency group and Anthropic credentials)")
    s.add_argument("question")
    s.add_argument("--model", default="claude-opus-5")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("chat", help="multi-turn conversation")
    s.add_argument("--model", default="claude-opus-5")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("serve", help="run the MCP server (stdio by default; --http to host it)")
    s.add_argument("--http", action="store_true", help="serve streamable HTTP at /mcp instead of stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
