"""Retrieval, and the statuses that make retrieval honest."""

from claudette.envelope import PROVENANCE, ToolResponse
from claudette.index import content_terms, fts_query


def test_every_response_carries_provenance_without_being_asked():
    for r in (ToolResponse.ok([]), ToolResponse.no_coverage("x"), ToolResponse.weak([], "y"), ToolResponse.not_found("z")):
        assert r.provenance == PROVENANCE
        assert "Answer only from these passages" in r.constraint


def test_content_terms_drop_stopwords_and_dedupe():
    assert content_terms("What does she think about the committee and the committee?") == ["committee"]


def test_fts_query_neutralises_syntax():
    assert fts_query(['a"b', "c"]) == '"ab" OR "c"'


def test_search_ok_returns_cited_hits(index):
    r = index.search("power over consent")
    assert r.status == "ok"
    top = r.data[0]
    assert top["author"] == "Ada Fixture"
    assert top["ref"] == f"fixture-committee§{top['ordinal']}"
    assert set(top["matched_terms"]) >= {"power", "consent"}


def test_search_uses_stemming(index):
    r = index.search("masters commanding")
    assert r.status == "ok"
    assert set(r.data[0]["matched_terms"]) == {"masters", "commanding"}


def test_two_word_query_needs_both_words(index):
    # "gate" is in the mill text; "spaceship" is nowhere. One of two is not coverage.
    r = index.search("gate spaceship")
    assert r.status == "weak"


def test_search_restricted_to_one_work(index):
    r = index.search("master", slug="fixture-mill")
    assert r.status == "ok"
    assert {h["author"] for h in r.data} == {"Beatrice Sample"}


def test_no_coverage_is_a_status_not_an_empty_list(index):
    r = index.search("cryptocurrency valuation")
    assert r.status == "no_coverage"
    assert r.data is None
    assert r.limitations and "cryptocurrency" in r.limitations[0]


def test_stripped_introduction_is_not_retrievable(index):
    assert index.search("zebra").status == "no_coverage"


def test_licence_text_is_not_retrievable(index):
    assert index.search("licence general terms").status in ("no_coverage", "weak")
    r = index.search("licence general terms")
    if r.data:
        assert all("Gutenberg" not in h["text"] for h in r.data)


def test_thin_match_is_flagged_weak(index):
    r = index.search("gate spaceship quasar neutrino")
    assert r.status == "weak"
    assert "1 of 4" in r.limitations[0]


def test_only_stopwords_is_no_coverage(index):
    assert index.search("what is it about?").status == "no_coverage"


def test_read_returns_neighbours_and_resolves_ref(index):
    hit = index.search("committee vote").data[0]
    r = index.read(hit["ref"], context=1)
    assert r.status == "ok"
    ords = [p["ordinal"] for p in r.data["passages"]]
    assert hit["ordinal"] in ords
    assert r.data["author"] == "Ada Fixture"


def test_read_bad_refs(index):
    assert index.read("nonsense").status == "not_found"
    assert index.read("no-such-work§0").status == "not_found"
    r = index.read("fixture-committee§9999")
    assert r.status == "not_found" and "does not exist" in r.limitations[0]


def test_works_and_stats(index):
    assert [w["slug"] for w in index.works()] == ["fixture-mill", "fixture-committee"]  # ordered by year
    assert [w["slug"] for w in index.works("fiction")] == ["fixture-mill"]
    s = index.stats()
    assert s == {"works": 2, "authors": 2, "passages": sum(w["passages"] for w in index.works())}
