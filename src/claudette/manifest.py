"""The manifest: the single place that decides what Claudette may quote.

Validation is strict on purpose. A work with a missing author does not load,
because an unattributed passage is exactly the thing this project exists to
rule out.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Shelf = str  # "thought" | "fiction" — kept open so a curator can add shelves.

# Two lanes, deliberately kept apart. A public-domain work is identified by a
# Gutenberg ID anyone can open. A local work is a file the operator supplied,
# with a rights note, and it never enters the shared release index. Mixing the
# two under one provenance statement would break the guarantee silently, so
# every passage carries its own `source` instead.
Lane = Literal["public-domain", "local"]


class Work(BaseModel):
    lane: Lane = Field(default="public-domain")
    id: int | None = Field(default=None, description="Project Gutenberg ebook number. Required for the public-domain lane.")
    slug: str = Field(description="Stable short identifier used in citations, e.g. 'follett-new-state'.")
    author: str = Field(min_length=1, description="The woman who wrote it. Required; nothing indexes without it.")
    title: str = Field(min_length=1)
    year: int | None = Field(default=None, description="Publication year where known. The full tier carries a rough era instead.")
    language: str = Field(default="en", description="ISO 639-1 code as Gutenberg records it.")
    curated: bool = Field(default=True, description="True for manifest entries a person wrote. False for entries generated from the catalogue.")
    shelf: Shelf = Field(description="Loose grouping: 'thought' or 'fiction'.")
    why: str = Field(min_length=1, description="One or two sentences on why this work is here. Shown to the model and to readers.")
    start_after: str | None = Field(default=None, description="If set, text before the first occurrence of this marker is discarded (used to drop prefaces by other hands).")
    end_before: str | None = Field(default=None, description="If set, text from the first occurrence of this marker onward is discarded.")
    rights: str | None = Field(default=None, description="Local lane only: where the text came from and on what basis you hold it. Required there.")
    source_file: str | None = Field(default=None, description="Local lane only: the file the text was taken from.")
    checksum: str | None = Field(default=None, description="Local lane only: sha256 prefix of the cleaned text, so `verify` can detect a changed file.")

    @model_validator(mode="after")
    def _lane_requirements(self) -> "Work":
        if self.lane == "public-domain":
            if self.id is None:
                raise ValueError(f"{self.slug}: public-domain works need a Gutenberg id")
            if self.rights or self.source_file:
                raise ValueError(f"{self.slug}: rights/source_file belong to the local lane")
        else:
            if not self.rights or not self.rights.strip():
                raise ValueError(f"{self.slug}: local works need a rights note — where the text came from and on what basis you hold it")
            if not self.source_file:
                raise ValueError(f"{self.slug}: local works need source_file")
            if self.id is not None or self.start_after or self.end_before:
                raise ValueError(f"{self.slug}: Gutenberg id and trim markers belong to the public-domain lane")
        return self

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        ok = all(c.islower() or c.isdigit() or c == "-" for c in v)
        if not ok or not v:
            raise ValueError(f"slug must be lowercase letters, digits and hyphens: {v!r}")
        return v

    @property
    def citation(self) -> str:
        return f"{self.author}, {self.title} ({self.year or 'n.d.'})"

    @property
    def gutenberg_url(self) -> str:
        return f"https://www.gutenberg.org/cache/epub/{self.id}/pg{self.id}.txt"

    @property
    def source(self) -> str:
        """One line a reader can act on: where this text came from and on what basis."""
        if self.lane == "public-domain":
            return f"Project Gutenberg #{self.id}, public domain"
        return f"Local file {self.source_file!r}; rights: {self.rights}"


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


def load_merged(pd_path: Path, local_path: Path | None) -> Manifest:
    """The public-domain manifest plus the operator's local one, if any.

    Slugs must be unique across both; the Manifest validator enforces it.
    """
    m = load_manifest(pd_path)
    if local_path is not None and local_path.exists():
        local = load_manifest(local_path)
        bad = [w.slug for w in local.works if w.lane != "local"]
        if bad:
            raise ValueError(f"{local_path}: works must be in the local lane: {bad}")
        m = Manifest(name=m.name, source=m.source, principle=m.principle, works=m.works + local.works)
    return m


def from_catalog(row) -> Work:
    """A generated entry for the full tier. Its `why` is the basis, not a judgement."""
    qids = row.basis.split(",")
    return Work(
        id=row.id,
        slug=row.slug,
        author=row.authors,
        title=row.title,
        year=row.year,
        language=row.language,
        shelf="uncurated",
        curated=False,
        why="Generated from the catalogue: every creator, editor and translator is recorded as a woman on Wikidata ("
        + ", ".join(qids) + "). Edition not reviewed by a person.",
    )


def toml_entry(work: Work) -> str:
    """Render one [[work]] block. Manifests are appended to as text so that
    hand-written comments survive; there is no TOML writer in the stdlib."""
    import json

    lines = ["[[work]]"]
    for key in ("lane", "id", "slug", "author", "title", "year", "shelf", "why", "start_after", "end_before", "rights", "source_file", "checksum"):
        val = getattr(work, key)
        if val is None or (key == "lane" and val == "public-domain"):
            continue
        lines.append(f"{key} = {val if isinstance(val, int) else json.dumps(val, ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
