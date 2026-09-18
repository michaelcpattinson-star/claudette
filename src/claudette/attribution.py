"""Verify an attribution against Wikidata.

Lens mode lets the model reason from what it already knows of women thinkers
of any era — Arendt, Ostrom, hooks — where the corpus cannot reach. The risk
is the classic one: a plausible author who does not exist, a book she never
wrote, a man remembered as a woman. This tool is the check. It is the one
network call the server makes after bootstrap, and it goes only to Wikidata.

It answers three questions: is there a person of this name; is she recorded
as a woman; did she write a work of this title. Results are cached for the
life of the process.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from claudette import __version__
from claudette.envelope import ToolResponse

API = "https://www.wikidata.org/w/api.php"
UA = f"claudette/{__version__} (attribution check; https://github.com/michaelcpattinson-star/claudette)"

HUMAN = "Q5"
WOMAN = {"Q6581072", "Q1052281"}  # female, trans woman
MAN = {"Q6581097", "Q2449503"}  # male, trans man

_cache: dict[str, dict] = {}


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _search(text: str, limit: int = 6) -> list[dict]:
    return _get({"action": "wbsearchentities", "search": text, "language": "en", "type": "item", "limit": limit}).get("search", [])


def _entities(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    return _get({"action": "wbgetentities", "ids": "|".join(ids), "props": "claims|labels|descriptions", "languages": "en"}).get("entities", {})


def _claim_ids(entity: dict, prop: str) -> list[str]:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(v, dict) and "id" in v:
            out.append(v["id"])
    return out


def _year(entity: dict, prop: str) -> int | None:
    for c in entity.get("claims", {}).get(prop, []):
        t = c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("time", "")
        m = re.match(r"[+-](\d{4})", t)
        if m:
            return int(m.group(1))
    return None


def find_person(name: str, *, fetch_search=_search, fetch_entities=_entities) -> dict | None:
    """The first search result that is a human. Returns a summary or None."""
    hits = fetch_search(name)
    ents = fetch_entities([h["id"] for h in hits])
    for h in hits:
        e = ents.get(h["id"], {})
        if HUMAN not in _claim_ids(e, "P31"):
            continue
        genders = _claim_ids(e, "P21")
        return {
            "qid": h["id"],
            "label": e.get("labels", {}).get("en", {}).get("value", h.get("label", name)),
            "description": e.get("descriptions", {}).get("en", {}).get("value", ""),
            "is_woman": True if any(g in WOMAN for g in genders) else False if any(g in MAN for g in genders) else None,
            "born": _year(e, "P569"),
            "died": _year(e, "P570"),
        }
    return None


def find_work(title: str, author_qid: str, author_label: str = "", *, fetch_search=_search, fetch_entities=_entities) -> dict | None:
    """A work of this title whose author (P50) is the person. Titles are shared
    by films and albums, so a second search appends the author's surname."""
    queries = [title]
    if author_label:
        queries.append(f"{title} {author_label.split()[-1]}")
    seen: set[str] = set()
    for q in queries:
        hits = [h for h in fetch_search(q, limit=10) if h["id"] not in seen]
        seen.update(h["id"] for h in hits)
        ents = fetch_entities([h["id"] for h in hits])
        for h in hits:
            e = ents.get(h["id"], {})
            if author_qid in _claim_ids(e, "P50"):
                return {
                    "qid": h["id"],
                    "label": e.get("labels", {}).get("en", {}).get("value", title),
                    "description": e.get("descriptions", {}).get("en", {}).get("value", ""),
                    "year": _year(e, "P577"),
                }
    return None


def verify(name: str, work: str | None = None, **fetchers) -> ToolResponse:
    key = f"{name}|{work or ''}"
    if key in _cache:
        return ToolResponse(**_cache[key])
    try:
        person = find_person(name, **fetchers)
    except Exception as e:  # network down: say so, never guess
        return ToolResponse.not_found(f"Wikidata unreachable ({e.__class__.__name__}); attribution unverified. Say so if you name her.")
    if person is None:
        resp = ToolResponse.not_found(f"No person named {name!r} on Wikidata. Do not attribute ideas to her; if you are sure she exists, tell the user the attribution is unverified.")
    else:
        data = {**person, "work": None}
        lims = []
        if person["is_woman"] is False:
            lims.append(f"{person['label']} is recorded as a man. Do not use his framework in Lens mode.")
        elif person["is_woman"] is None:
            lims.append(f"{person['label']} has no recorded gender on Wikidata. Treat as unverified.")
        if work:
            try:
                w = find_work(work, person["qid"], person["label"], **fetchers)
            except Exception as e:
                w = None
                lims.append(f"Wikidata unreachable while checking the work ({e.__class__.__name__}).")
            data["work"] = w
            if w is None and not lims:
                lims.append(f"No work titled {work!r} by {person['label']} found on Wikidata. It may exist under another title; say the title is unverified, or drop it.")
        status = "ok" if person["is_woman"] is True and (not work or data["work"]) else "weak"
        resp = ToolResponse(status=status, data=data, limitations=lims)
    _cache[key] = resp.model_dump()
    return resp
