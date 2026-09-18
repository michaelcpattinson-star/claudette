"""The full tier: every catalogued text, fetched from Gutenberg's mirror and
indexed locally.

This is opt-in and slow — nine thousand texts, a few GB, an hour or more —
so it never runs on its own. The download is resumable (files already on disk
are skipped) and the build is a single pass at the end, so an interrupted
`expand` loses nothing but time.

Transport: rsync against Gutenberg's sanctioned mirror, one connection for
the whole list, which is what the project asks bulk users to do. Anything
the mirror lacks (a handful of very old or very new files) falls back to
HTTP with a pause between requests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from claudette import catalog_path, full_db_path, manifest_path, texts_dir, texts_full_dir
from claudette.catalog import FullWork, read_catalog
from claudette.fetch import USER_AGENT, clean_path, strip_boilerplate
from claudette.index import build
from claudette.manifest import Manifest, Work, from_catalog, load_manifest

# One of Gutenberg's official rsync mirrors; override with CLAUDETTE_MIRROR.
# (ftp.ibiblio.org refused a large --files-from list when this was written.)
MIRROR = os.environ.get("CLAUDETTE_MIRROR", "aleph.gutenberg.org::gutenberg")


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def mirror_path(ebook_id: int) -> str:
    """Gutenberg's old layout: every digit but the last as directories, then the id."""
    s = str(ebook_id)
    head = "/".join(s[:-1]) if len(s) > 1 else "0"
    return f"{head}/{s}"


def candidate_files(ebook_id: int) -> list[str]:
    p = mirror_path(ebook_id)
    return [f"{p}/{ebook_id}-0.txt", f"{p}/{ebook_id}-8.txt", f"{p}/{ebook_id}.txt"]


def raw_on_disk(dest: Path, ebook_id: int) -> Path | None:
    for rel in candidate_files(ebook_id):
        p = dest / rel
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def rsync_fetch(works: list[FullWork], dest: Path, *, log=log) -> None:
    """Two rsync passes: the UTF-8 `-0.txt` every modern text has, then the
    older encodings for whatever is still missing. Missing files are expected
    and ignored; asking for them all at once makes the sender stat three paths
    per work and roughly triples the wall-clock."""
    if shutil.which("rsync") is None:
        log("rsync not found; falling back to HTTP for everything (slower)")
        return
    dest.mkdir(parents=True, exist_ok=True)
    for variant in (0, slice(1, None)):
        pending = [w for w in works if raw_on_disk(dest, w.id) is None]
        if not pending:
            return
        wanted = []
        for w in pending:
            c = candidate_files(w.id)
            wanted += [c[variant]] if variant == 0 else c[variant]
        listing = dest / ".files-from.txt"
        listing.write_text("\n".join(wanted) + "\n")
        log(f"rsync: {len(wanted)} files for {len(pending)} works from {MIRROR}")
        cmd = ["rsync", "-az", "--no-motd", "--timeout=300", "--ignore-errors", "--files-from", str(listing), f"{MIRROR}/", str(dest)]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        # exit 23 = some files missing, which is normal here; anything else is a real failure
        if proc.returncode not in (0, 23, 24):
            log(f"rsync failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
            return


def http_fetch_missing(works: list[FullWork], dest: Path, *, pause: float = 3.0, log=log) -> int:
    missing = [w for w in works if raw_on_disk(dest, w.id) is None]
    if not missing:
        return 0
    log(f"http: {len(missing)} works not on the mirror; fetching politely ({pause}s apart)")
    got = 0
    for i, w in enumerate(missing, 1):
        url = f"https://www.gutenberg.org/cache/epub/{w.id}/pg{w.id}.txt"
        target = dest / candidate_files(w.id)[0]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            got += 1
        except Exception as e:
            log(f"  {w.id}: {e.__class__.__name__}")
        if i % 50 == 0:
            log(f"  http {i}/{len(missing)}")
        time.sleep(pause)
    return got


def select(works: list[FullWork], *, languages: set[str] | None, limit: int | None, exclude_ids: set[int]) -> list[FullWork]:
    out = [w for w in works if w.id not in exclude_ids and (not languages or w.language in languages)]
    return out[:limit] if limit else out


def expand(*, languages: set[str] | None = None, limit: int | None = None, skip_fetch: bool = False, log=log) -> Path:
    """Fetch and index the full tier. Returns the path of the built index."""
    core = load_manifest(manifest_path())
    core_ids = {w.id for w in core.works if w.id is not None}
    catalog = read_catalog(catalog_path())
    chosen = select(catalog, languages=languages, limit=limit, exclude_ids=core_ids)
    dest = texts_full_dir()
    log(f"full tier: {len(chosen)} catalogued works (+ {len(core.works)} curated)")

    if not skip_fetch:
        rsync_fetch(chosen, dest, log=log)
        http_fetch_missing(chosen, dest, log=log)
    have = sum(1 for w in chosen if raw_on_disk(dest, w.id))
    log(f"texts on disk: {have}/{len(chosen)}")

    works: list[Work] = list(core.works) + [from_catalog(w) for w in chosen]
    manifest = Manifest(name=core.name, source=core.source, principle=core.principle, works=works)
    by_id = {w.id: w for w in chosen}

    def text_for(work: Work) -> str | None:
        if work.curated:
            p = clean_path(texts_dir(), work)
            return p.read_text(encoding="utf-8") if p.exists() else None
        raw = raw_on_disk(dest, work.id)
        if raw is None:
            return None
        data = raw.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return strip_boilerplate(text, None)

    out = full_db_path()
    log(f"indexing → {out} (this is the slow part)")
    counts = build(manifest, text_for, out, tier="full", skip_missing=True, log=log)
    log(f"full tier ready: {len(counts)} works, {sum(counts.values())} passages")
    return out
