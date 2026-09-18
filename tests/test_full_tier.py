"""The full tier: catalogue rule, mirror paths, and a generated build."""

from pathlib import Path

import pytest

from claudette.catalog import Author, FullWork, parse_ebook, read_catalog, slugify, write_catalog
from claudette.expand import candidate_files, mirror_path, select
from claudette.index import Index, build
from claudette.manifest import Manifest, from_catalog

RDF = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dcterms="http://purl.org/dc/terms/"
         xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/" xmlns:marcrel="http://id.loc.gov/vocabulary/relators/">
  <pgterms:ebook rdf:about="ebooks/{id}">
    <dcterms:title>{title}</dcterms:title>
    <dcterms:type><rdf:Description><rdf:value>{type}</rdf:value></rdf:Description></dcterms:type>
    <dcterms:language><rdf:Description><rdf:value>en</rdf:value></rdf:Description></dcterms:language>
    {roles}
  </pgterms:ebook>
</rdf:RDF>
"""


def _agent(role: str, agent_id: int) -> str:
    return f'<{role}><pgterms:agent rdf:about="2009/agents/{agent_id}"><pgterms:name>x</pgterms:name></pgterms:agent></{role}>'


WOMEN = {
    10: Author(10, "Q10", "Ada Fixture", 1850, 1920),
    11: Author(11, "Q11", "Beatrice Sample", None, None),
}


def _parse(**kw):
    kw.setdefault("id", 500)
    kw.setdefault("title", "A Book")
    kw.setdefault("type", "Text")
    return parse_ebook(RDF.format(**kw).encode(), WOMEN)


def test_single_woman_author_qualifies():
    w = _parse(roles=_agent("dcterms:creator", 10))
    assert w and w.authors == "Ada Fixture" and w.basis == "Q10" and w.year == 1880
    assert w.slug == "fixture-a-book-500"


def test_male_or_unknown_creator_disqualifies():
    assert _parse(roles=_agent("dcterms:creator", 99)) is None
    assert _parse(roles=_agent("dcterms:creator", 10) + _agent("dcterms:creator", 99)) is None


def test_editor_or_translator_must_also_be_a_woman():
    assert _parse(roles=_agent("dcterms:creator", 10) + _agent("marcrel:edt", 99)) is None
    assert _parse(roles=_agent("dcterms:creator", 10) + _agent("marcrel:trl", 99)) is None
    w = _parse(roles=_agent("dcterms:creator", 10) + _agent("marcrel:trl", 11))
    assert w and w.authors == "Ada Fixture; Beatrice Sample"


def test_illustrator_is_ignored_and_anonymous_excluded():
    assert _parse(roles=_agent("dcterms:creator", 10) + _agent("marcrel:ill", 99)) is not None
    assert _parse(roles="") is None
    assert _parse(roles=_agent("dcterms:creator", 10), type="Sound") is None


def test_slugify_strips_accents():
    assert slugify("Élisabeth Brontë!") == "elisabeth-bronte"


def test_catalog_roundtrip(tmp_path):
    works = [FullWork(7, "a-b-7", "A", "B", "en", None, "Q1"), FullWork(8, "c-d-8", "C; D", "D, vol. 1", "fr", 1900, "Q2,Q3")]
    p = tmp_path / "w.csv"
    write_catalog(works, p, generated="test")
    assert read_catalog(p) == works


def test_mirror_paths():
    assert mirror_path(73755) == "7/3/7/5/73755"
    assert mirror_path(45) == "4/45"
    assert mirror_path(5) == "0/5"
    assert candidate_files(1342)[0] == "1/3/4/1342/1342-0.txt"


def test_select_excludes_core_and_filters_language():
    ws = [FullWork(1, "a", "A", "T", "en", None, "Q"), FullWork(2, "b", "B", "T", "fr", None, "Q"), FullWork(3, "c", "C", "T", "en", None, "Q")]
    assert [w.id for w in select(ws, languages={"en"}, limit=None, exclude_ids={1})] == [3]
    assert [w.id for w in select(ws, languages=None, limit=2, exclude_ids=set())] == [1, 2]


def test_full_tier_build_marks_generated_works_uncurated(tmp_path):
    gen = from_catalog(FullWork(900, "fixture-later-900", "Ada Fixture", "Later", "en", 1885, "Q10"))
    assert gen.curated is False and gen.shelf == "uncurated" and "Q10" in gen.why and gen.source == "Project Gutenberg #900, public domain"
    m = Manifest(name="t", source="t", principle="t", works=[gen])
    db = tmp_path / "full.db"
    build(m, lambda w: "The committee met again, and again found power with rather than over.", db, tier="full", skip_missing=True, log=lambda *_: None)
    idx = Index(db)
    assert idx.tier == "full"
    hit = idx.search("committee power").data[0]
    assert hit["curated"] is False and hit["ref"] == "fixture-later-900§0"
    assert idx.search("committee", curated_only=True).status == "no_coverage"


def test_skip_missing_leaves_a_work_out_instead_of_aborting(tmp_path):
    a = from_catalog(FullWork(1, "a-x-1", "A", "X", "en", None, "Q1"))
    b = from_catalog(FullWork(2, "b-y-2", "B", "Y", "en", None, "Q2"))
    m = Manifest(name="t", source="t", principle="t", works=[a, b])
    counts = build(m, lambda w: "some text" if w.id == 2 else None, tmp_path / "f.db", tier="full", skip_missing=True, log=lambda *_: None)
    assert list(counts) == ["b-y-2"]
    with pytest.raises(FileNotFoundError):
        build(m, lambda w: None, tmp_path / "g.db", tier="full", skip_missing=False, log=lambda *_: None)
