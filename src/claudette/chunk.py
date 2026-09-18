"""Split a text into passages that can be cited.

A passage is the unit of retrieval and of citation: "[Author, Title §412]"
means passage 412 of that work, and `read_passage` will hand it back
verbatim with its neighbours. Passages follow paragraph boundaries so that a
quotation is never cut mid-sentence by the chunker; very long paragraphs
are split at sentence ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 900   # characters we aim for per passage
HARD_MAX = 1800  # split a single paragraph above this

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’)])\s+")


@dataclass(frozen=True)
class Passage:
    ordinal: int
    text: str


def paragraphs(text: str) -> list[str]:
    out = []
    for block in _PARA_SPLIT.split(text):
        p = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if p:
            out.append(p)
    return out


def _split_long(p: str) -> list[str]:
    if len(p) <= HARD_MAX:
        return [p]
    pieces, buf = [], ""
    for sent in _SENT_END.split(p):
        if buf and len(buf) + len(sent) + 1 > TARGET:
            pieces.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def chunk(text: str) -> list[Passage]:
    """Merge short paragraphs up to TARGET; split long ones at sentences."""
    units: list[str] = []
    for p in paragraphs(text):
        units.extend(_split_long(p))

    passages: list[Passage] = []
    buf = ""
    for u in units:
        if buf and len(buf) + len(u) + 1 > TARGET:
            passages.append(Passage(len(passages), buf))
            buf = u
        else:
            buf = f"{buf}\n{u}".strip() if buf else u
    if buf:
        passages.append(Passage(len(passages), buf))
    return passages
