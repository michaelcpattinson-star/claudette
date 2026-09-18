"""The response envelope every tool returns.

Two design decisions live here.

First, `no_coverage` is a distinct status from an empty `ok`. A model handed
an empty list narrates it as "the corpus has nothing to say", which may be
true, or it may mean the query used words the 1850s did not. The status
says which, and `weak` sits between them for a thin match that should be
reported as thin.

Second, the provenance line is written by this module, not passed in by the
tool. A marking a caller can forget is a marking that will be forgotten, and
the whole point of Claudette is that the reader can always see whose words
these are.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "weak", "no_coverage", "not_found"]

PROVENANCE = "Every passage is from a work by a named woman author. Each hit's `source` says where that text came from and on what basis; `curated` says whether a person reviewed the edition."

CONSTRAINT = (
    "Answer only from these passages. Quote or closely paraphrase, and cite each "
    "claim as [Author, Title §n]. If the passages do not speak to the question, say "
    "so plainly rather than filling the gap from elsewhere."
)


class Hit(BaseModel):
    """One retrieved passage, with everything needed to cite and to check it."""

    ref: str = Field(description="Citation key, e.g. 'follett-new-state§412'. Pass to read_passage.")
    author: str
    title: str
    year: int | None
    ordinal: int = Field(description="Passage number within the work.")
    text: str
    matched_terms: list[str] = Field(description="Query terms found in this passage. Fewer than half the query's terms means a thin match.")
    source: str = Field(description="Where this text came from and on what basis, e.g. 'Project Gutenberg #1342, public domain'.")
    curated: bool = Field(description="True if a person reviewed this edition and wrote its manifest entry; False for the generated full tier, whose front matter has not been checked.")


class ToolResponse(BaseModel):
    status: Status = Field(
        description=(
            "ok — passages found that match most of the question's terms. "
            "weak — something matched, but thinly; say so if you use it. "
            "no_coverage — the corpus does not speak to this. Say that. Do not answer from elsewhere. "
            "not_found — a specific reference did not resolve."
        )
    )
    data: Any = None
    provenance: str = Field(default=PROVENANCE)
    constraint: str = Field(default=CONSTRAINT)
    limitations: list[str] = Field(default_factory=list, description="Part of the answer, not a disclaimer.")

    @classmethod
    def ok(cls, data: Any, limitations: list[str] | None = None) -> "ToolResponse":
        return cls(status="ok", data=data, limitations=limitations or [])

    @classmethod
    def weak(cls, data: Any, why: str) -> "ToolResponse":
        return cls(status="weak", data=data, limitations=[why])

    @classmethod
    def no_coverage(cls, why: str) -> "ToolResponse":
        return cls(status="no_coverage", data=None, limitations=[why])

    @classmethod
    def not_found(cls, why: str) -> "ToolResponse":
        return cls(status="not_found", data=None, limitations=[why])
