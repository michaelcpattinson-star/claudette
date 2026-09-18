"""Get an index onto disk.

A connector must work the first time someone adds it, without a build step.
So on startup, if there is no index: try the prebuilt one published with the
release (one download, a few tens of MB); failing that, fetch the corpus from
Gutenberg and build it here (a few minutes). Either way the result is the
same file, reproducible by anyone from the manifest.

Nothing in here writes to stdout — in stdio mode that is the MCP transport.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

from claudette import __version__, active_db_path, db_path, manifest_path, texts_dir
from claudette.manifest import load_manifest

# The release that carries the current core index. Bumped only when the core
# index itself changes (new works, new schema) — not on every code release.
INDEX_RELEASE = "v0.2.0"
RELEASE_INDEX_URL = os.environ.get(
    "CLAUDETTE_INDEX_URL",
    f"https://github.com/michaelcpattinson-star/claudette/releases/download/{INDEX_RELEASE}/claudette.db",
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _download_prebuilt(dest: Path) -> bool:
    if os.environ.get("CLAUDETTE_NO_PREBUILT"):
        return False
    tmp = dest.with_suffix(".db.part")
    try:
        log(f"claudette: downloading prebuilt index from {RELEASE_INDEX_URL}")
        req = urllib.request.Request(RELEASE_INDEX_URL, headers={"User-Agent": f"claudette/{__version__}"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        tmp.replace(dest)
        return True
    except Exception as e:  # any failure falls through to a local build
        log(f"claudette: prebuilt index unavailable ({e.__class__.__name__}: {e}); building locally")
        tmp.unlink(missing_ok=True)
        return False


def _build_locally(dest: Path) -> None:
    from claudette.fetch import clean_path, fetch_all
    from claudette.index import build

    m = load_manifest(manifest_path())
    log(f"claudette: fetching {len(m.works)} works from Project Gutenberg into {texts_dir()}")
    fetch_all(m, texts_dir(), log=log)
    log("claudette: building index")

    def text_for(w):
        p = clean_path(texts_dir(), w)
        return p.read_text(encoding="utf-8") if p.exists() else None

    build(m, text_for, dest, tier="core", log=log)


SCHEMA_VERSION = "2"  # bump when the index layout changes; stale files are rebuilt


def _current_format(path: Path) -> bool:
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        con.close()
        return row is not None and row[0] == SCHEMA_VERSION
    except sqlite3.Error:
        return False


def ensure_index() -> Path:
    """The full tier if `claudette expand` has built it, else the core — fetched if needed.

    An index in an older layout is removed and fetched or built again, so an
    upgrade never leaves a server that cannot read its own files.
    """
    active = active_db_path()
    if active.exists():
        if _current_format(active):
            return active
        log(f"claudette: {active.name} is in an older format; refreshing")
        active.unlink()
        active = active_db_path()
        if active.exists() and _current_format(active):
            return active
    dest = db_path()
    if not _download_prebuilt(dest):
        _build_locally(dest)
    log(f"claudette: index ready at {dest}")
    return dest
