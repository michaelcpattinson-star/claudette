"""The manifest: the single place that decides what Claudette may quote.

Validation is strict on purpose. A work with a missing author does not load,
because an unattributed passage is exactly the thing this project exists to
rule out.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

Shelf = str  # "thought" | "fiction" — kept open so a curator can add shelves.


class Work(BaseModel):
    id: int = Field(description="Project Gutenberg ebook number.")
    slug: str = Field(description="Stable short identifier used in citations, e.g. 'follett-new-state'.")
    author: str = Field(min_length=1, description="The woman who wrote it. Required; nothing indexes without it.")
    title: str = Field(min_length=1)
    year: int
    shelf: Shelf = Field(description="Loose grouping: 'thought' or 'fiction'.")
    why: str = Field(min_length=1, description="One or two sentences on why this work is here. Shown to the model and to readers.")
    start_after: str | None = Field(default=None, description="If set, text before the first occurrence of this marker is discarded (used to drop prefaces by other hands).")
    end_before: str | None = Field(default=None, description="If set, text from the first occurrence of this marker onward is discarded.")

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        ok = all(c.islower() or c.isdigit() or c == "-" for c in v)
        if not ok or not v:
            raise ValueError(f"slug must be lowercase letters, digits and hyphens: {v!r}")
        return v

    @property
    def citation(self) -> str:
        return f"{self.author}, {self.title} ({self.year})"

    @property
    def gutenberg_url(self) -> str:
        return f"https://www.gutenberg.org/cache/epub/{self.id}/pg{self.id}.txt"


class Manifest(BaseModel):
    name: str
    source: str
    principle: str
    works: list[Work]

    @field_validator("works")
    @classmethod
    def _unique(cls, works: list[Work]) -> list[Work]:
        ids = [w.id for w in works]
        slugs = [w.slug for w in works]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate Gutenberg ids in manifest: {dupes}")
        if len(set(slugs)) != len(slugs):
            dupes = sorted({s for s in slugs if slugs.count(s) > 1})
            raise ValueError(f"duplicate slugs in manifest: {dupes}")
        if not works:
            raise ValueError("manifest lists no works")
        return works

    def by_slug(self, slug: str) -> Work | None:
        return next((w for w in self.works if w.slug == slug), None)

    def authors(self) -> list[str]:
        seen: dict[str, None] = {}
        for w in self.works:
            seen.setdefault(w.author, None)
        return list(seen)


def load_manifest(path: Path) -> Manifest:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    meta = raw.get("corpus", {})
    return Manifest(
        name=meta.get("name", "corpus"),
        source=meta.get("source", ""),
        principle=meta.get("principle", ""),
        works=[Work(**w) for w in raw.get("work", [])],
    )
