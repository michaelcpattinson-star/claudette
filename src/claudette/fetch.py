"""Download works and strip Project Gutenberg boilerplate.

Network access happens here and only here. Everything downstream works from
files on disk, so the index and the server can be audited offline.
"""

from __future__ import annotations

import re
import time
import urllib.request
from pathlib import Path

from claudette.manifest import Manifest, Work

START_RE = re.compile(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M)
END_RE = re.compile(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M)
HEADER_TITLE_RE = re.compile(r"^The Project Gutenberg eBook of (.+?)\s*$", re.M)

# Older Gutenberg files carry transcriber credits inside the START/END
# markers. They are names, not the author's prose, and they must not be
# retrievable as if they were. Only the leading paragraphs are examined.
_TRANSCRIBER_RE = re.compile(
    r"Produced by|Transcribe[rd]|This e-?text|E-?text prepared|Distributed Proofreading|"
    r"\*END\*|deHTML|digitized version|put on-line|HTML version by|For Project Gutenberg|"
    r"Celebration of Women Writers|\[Editor:",
    re.I,
)
_LEADING_PARAS_TO_EXAMINE = 12
_CREDIT_MAX_CHARS = 400  # credits are short; a real paragraph that mentions "transcribed" is not

USER_AGENT = "claudette/0.1 (+https://github.com/; corpus builder; polite sequential fetch)"


def raw_path(texts_dir: Path, work: Work) -> Path:
    return texts_dir / f"{work.id}.raw.txt"


def clean_path(texts_dir: Path, work: Work) -> Path:
    return texts_dir / f"{work.slug}.txt"


def download(work: Work, dest: Path, *, timeout: int = 60) -> None:
    req = urllib.request.Request(work.gutenberg_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def header_title(raw: str) -> str | None:
    """The title Gutenberg itself prints in the first line. Used by `verify`."""
    m = HEADER_TITLE_RE.search(raw[:2000])
    return m.group(1).strip() if m else None


def strip_boilerplate(raw: str, work: Work | None = None) -> str:
    """Return only the text of the work.

    Cuts Gutenberg's licence header and footer, then applies the work's own
    `start_after` / `end_before` trims if present. Raises if a declared marker
    is not found, because a silently ignored trim is how a preface by the
    wrong author gets in.
    """
    text = raw.replace("\r\n", "\n")
    m = START_RE.search(text)
    if m:
        text = text[m.end():]
    m = END_RE.search(text)
    if m:
        text = text[: m.start()]
    text = _drop_transcriber_credits(text)
    if work is not None:
        if work.start_after:
            idx = text.find(work.start_after)
            if idx < 0:
                raise ValueError(f"{work.slug}: start_after marker not found: {work.start_after!r}")
            text = text[idx + len(work.start_after):]
        if work.end_before:
            idx = text.find(work.end_before)
            if idx < 0:
                raise ValueError(f"{work.slug}: end_before marker not found: {work.end_before!r}")
            text = text[:idx]
    return text.strip() + "\n"


def _drop_transcriber_credits(text: str) -> str:
    """Remove leading paragraphs that are transcriber notes rather than the work."""
    paras = re.split(r"(\n\s*\n)", text)  # keep separators so the join is lossless
    out, examined = [], 0
    for piece in paras:
        if piece.strip() and examined < _LEADING_PARAS_TO_EXAMINE:
            examined += 1
            if len(piece) <= _CREDIT_MAX_CHARS and _TRANSCRIBER_RE.search(piece):
                continue
        out.append(piece)
    return "".join(out)


def fetch_all(manifest: Manifest, texts_dir: Path, *, force: bool = False, pause: float = 1.0, log=print) -> list[Work]:
    """Fetch every work not already on disk. Returns the works fetched."""
    fetched: list[Work] = []
    for work in manifest.works:
        if work.lane != "public-domain":
            continue
        raw = raw_path(texts_dir, work)
        if raw.exists() and not force:
            log(f"  have   {work.slug}")
        else:
            log(f"  fetch  {work.slug}  (gutenberg #{work.id})")
            download(work, raw)
            fetched.append(work)
            time.sleep(pause)  # be a polite guest
        clean = clean_path(texts_dir, work)
        clean.write_text(strip_boilerplate(raw.read_text(encoding="utf-8", errors="replace"), work), encoding="utf-8")
    return fetched


def verify_all(manifest: Manifest, texts_dir: Path, *, show: int = 0, log=print) -> list[str]:
    """Check each downloaded header title against the manifest. Returns problems."""
    problems: list[str] = []
    for work in manifest.works:
        if work.lane != "public-domain":
            continue
        raw = raw_path(texts_dir, work)
        if not raw.exists():
            problems.append(f"{work.slug}: not fetched")
            continue
        head = raw.read_text(encoding="utf-8", errors="replace")
        got = header_title(head)
        if got is None:
            problems.append(f"{work.slug}: no Gutenberg header found")
            continue
        # Compare loosely: Gutenberg's header often carries only the main
        # title, and punctuation varies. One must be a prefix of the other.
        a, b = _norm(work.title), _norm(got)
        n = min(len(a), len(b), 40)
        if n < 4 or a[:n] != b[:n]:
            problems.append(f"{work.slug}: header says {got!r}, manifest says {work.title!r}")
        else:
            log(f"  ok     {work.slug:36s} {got}")
        if show:
            clean = clean_path(texts_dir, work)
            if clean.exists():
                lines = [l for l in clean.read_text(encoding="utf-8").splitlines() if l.strip()][:show]
                for l in lines:
                    log(f"         | {l[:100]}")
    return problems


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
