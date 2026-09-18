"""A tiny fixture corpus, built the same way the real one is, with no network.

The fixture texts are written for the tests rather than excerpted, so the
suite is self-contained. They wear Gutenberg's boilerplate so the stripping
code is exercised for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claudette.index import Index, build
from claudette.manifest import Manifest, Work

GUTENBERG_WRAP = """The Project Gutenberg eBook of {title}

This eBook is for the use of anyone anywhere.

*** START OF THE PROJECT GUTENBERG EBOOK {upper} ***

{body}

*** END OF THE PROJECT GUTENBERG EBOOK {upper} ***

Licence text that must never be indexed. Section 1. General Terms of Use.
"""

FIXTURE_WORKS = [
    Work(
        id=1, slug="fixture-committee", author="Ada Fixture", title="On the Committee", year=1900, shelf="thought",
        why="Fixture on group organisation.",
        start_after="PART I",
    ),
    Work(
        id=2, slug="fixture-mill", author="Beatrice Sample", title="The Mill at Dawn", year=1850, shelf="fiction",
        why="Fixture on industry.",
    ),
]

FIXTURE_TEXT = {
    "fixture-committee": """INTRODUCTION BY SOMEONE ELSE

This introduction was written by another hand and must not be indexed. It
mentions the word zebra, which appears nowhere in the work proper.

PART I

The committee does not vote. It talks until the members find the thing that
none of them brought into the room, and that thing is the decision.

Power over another person is the weakest kind of power, because it ends where
his consent ends. Power with him has no such edge.

The master who commands and the man who obeys have made an arrangement, not a
relation. A relation begins when each can change the other.
""",
    "fixture-mill": """CHAPTER ONE

The mill stood at the edge of the town, and at dawn the hands went in through
the gate in a long line, and the gate was shut behind them.

Margaret watched from the window and thought that a master who never saw the
faces of his hands could not be expected to know their names.

""" + ("It was a long chapter, and the sentences went on. " * 60) + """

CHAPTER TWO

The strike began on a Tuesday.
""",
}


@pytest.fixture(scope="session")
def fixture_home(tmp_path_factory) -> Path:
    home = tmp_path_factory.mktemp("claudette-home")
    texts = home / "texts"
    texts.mkdir()
    from claudette.fetch import clean_path, raw_path, strip_boilerplate

    manifest = Manifest(name="fixture", source="tests", principle="fixture", works=FIXTURE_WORKS)
    for w in manifest.works:
        raw = GUTENBERG_WRAP.format(title=w.title, upper=w.title.upper(), body=FIXTURE_TEXT[w.slug])
        raw_path(texts, w).write_text(raw)
        clean_path(texts, w).write_text(strip_boilerplate(raw, w))

    def text_for(w):
        p = clean_path(texts, w)
        return p.read_text() if p.exists() else None

    build(manifest, text_for, home / "claudette.db", tier="core", log=lambda *_: None)
    return home


@pytest.fixture
def index(fixture_home: Path) -> Index:
    return Index(fixture_home / "claudette.db")


@pytest.fixture
def fixture_manifest() -> Manifest:
    return Manifest(name="fixture", source="tests", principle="fixture", works=FIXTURE_WORKS)
