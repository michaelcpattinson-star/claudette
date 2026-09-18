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

from claudette import __version__, db_path, manifest_path, texts_dir
from claudette.manifest import load_manifest

RELEASE_INDEX_URL = os.environ.get(
    "CLAUDETTE_INDEX_URL",
    f"https://github.com/michaelcpattinson-star/claudette/releases/download/v{__version__}/claudette.db",
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
    from claudette.fetch import fetch_all
    from claudette.index import build

    m = load_manifest(manifest_path())
    log(f"claudette: fetching {len(m.works)} works from Project Gutenberg into {texts_dir()}")
    fetch_all(m, texts_dir(), log=log)
    log("claudette: building index")
    build(m, texts_dir(), dest, log=log)


def ensure_index() -> Path:
    dest = db_path()
    if dest.exists():
        return dest
    if not _download_prebuilt(dest):
        _build_locally(dest)
    log(f"claudette: index ready at {dest}")
    return dest
