"""verify_attribution, against canned Wikidata responses."""

from claudette.attribution import verify

PEOPLE = {
    "Q158071": {"labels": {"en": {"value": "Elinor Ostrom"}}, "descriptions": {"en": {"value": "American political economist"}},
                "claims": {"P31": [_c := {"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}], "P21": [{"mainsnak": {"datavalue": {"value": {"id": "Q6581072"}}}}],
                           "P569": [{"mainsnak": {"datavalue": {"value": {"time": "+1933-08-07T00:00:00Z"}}}}]}},
    "Q9200": {"labels": {"en": {"value": "Max Weber"}}, "descriptions": {"en": {"value": "German sociologist"}},
              "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}], "P21": [{"mainsnak": {"datavalue": {"value": {"id": "Q6581097"}}}}]}},
    "Q1": {"labels": {"en": {"value": "universe"}}, "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q1454986"}}}}]}},
    "W1": {"labels": {"en": {"value": "Governing the Commons"}}, "descriptions": {"en": {"value": "book"}},
           "claims": {"P50": [{"mainsnak": {"datavalue": {"value": {"id": "Q158071"}}}}], "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+1990-01-01T00:00:00Z"}}}}]}},
}
SEARCH = {
    "Elinor Ostrom": [{"id": "Q158071"}],
    "Max Weber": [{"id": "Q9200"}],
    "universe": [{"id": "Q1"}],
    "Governing the Commons": [{"id": "W1"}],
    "Nobody Realberg": [],
    "A Book She Never Wrote": [{"id": "Q1"}],
}


def _search(text, limit=6):
    return SEARCH.get(text, [])


def _entities(ids):
    return {i: PEOPLE[i] for i in ids if i in PEOPLE}


F = dict(fetch_search=_search, fetch_entities=_entities)


def test_woman_and_her_work_verify_ok():
    r = verify("Elinor Ostrom", "Governing the Commons", **F)
    assert r.status == "ok"
    assert r.data["is_woman"] is True and r.data["born"] == 1933
    assert r.data["work"]["year"] == 1990


def test_man_is_flagged_and_barred():
    r = verify("Max Weber", **F)
    assert r.status == "weak" and r.data["is_woman"] is False
    assert "recorded as a man" in r.limitations[0]


def test_unknown_person_is_not_found():
    r = verify("Nobody Realberg", **F)
    assert r.status == "not_found" and "Do not attribute" in r.limitations[0]


def test_non_human_result_is_skipped():
    assert verify("universe", **F).status == "not_found"


def test_missing_work_is_weak_with_a_caveat():
    r = verify("Elinor Ostrom", "A Book She Never Wrote", **F)
    assert r.status == "weak" and r.data["work"] is None
    assert "unverified" in r.limitations[0]


def test_network_failure_says_so_rather_than_guessing():
    def boom(*a, **k):
        raise OSError("offline")

    r = verify("Elinor Ostrom", fetch_search=boom, fetch_entities=_entities)
    assert r.status == "not_found" and "unreachable" in r.limitations[0]
