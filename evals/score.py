"""Grade a raw eval run. No model involved: the checks are mechanical.

A question passes when the answer does what its `expect` says:
  cited     ≥1 citation, all verified against retrieved passages, and the
            required author is among them.
  refuse    no citations, and the answer contains a refusal phrase.
  partial   citations allowed, but the answer must flag the match as partial.
  refuse_or_partial   either of the above.
  any       recorded, not graded.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
CITE = re.compile(r"\[[^\[\]]*?§\s*\d+\]")
REFUSAL = re.compile(
    r"(do(es)? not|don't|didn't|did not|doesn't) (speak|write|cover|address|say)|predates|not in (the|this) corpus|nothing in (the|this) corpus|no passage",
    re.I,
)
PARTIAL = re.compile(r"partial|thin|only loosely|indirect|does not name|closest|nearest", re.I)


def grade(r: dict) -> tuple[str, str]:
    cites = CITE.findall(r["answer"])
    unverified = r["unverified_citations"]
    exp = r["expect"]
    refused = bool(REFUSAL.search(r["answer"]))
    partial = bool(PARTIAL.search(r["answer"]))

    def cited_ok() -> tuple[bool, str]:
        if not cites:
            return False, "no citations"
        if unverified:
            return False, f"unverified citations: {unverified}"
        want = r.get("must_cite_author")
        if want and not any(want.lower() in c.lower() for c in cites):
            return False, f"did not cite {want}"
        return True, f"{len(cites)} verified citations"

    def refuse_ok() -> tuple[bool, str]:
        if cites and not partial:
            return False, "cited despite being out of scope"
        if not refused:
            return False, "no refusal phrase"
        return True, "refused"

    if exp == "cited":
        ok, why = cited_ok()
    elif exp == "refuse":
        ok, why = refuse_ok()
    elif exp == "partial":
        ok, why = (bool(cites) and not unverified and partial, "partial-flagged" if partial else "not flagged as partial")
    elif exp == "refuse_or_partial":
        ok1, why1 = refuse_ok()
        ok2 = bool(cites) and not unverified and partial
        ok, why = (ok1 or ok2, why1 if ok1 else ("partial-flagged" if ok2 else f"{why1}; not flagged partial"))
    else:
        ok, why = True, "not graded"
    return ("PASS" if ok else "FAIL"), why


def main(path: str) -> int:
    raw = json.loads(Path(path).read_text())
    rows = []
    for r in raw["results"]:
        verdict, why = grade(r)
        rows.append({"id": r["id"], "expect": r["expect"], "verdict": verdict, "why": why})
        print(f"{verdict:4s} {r['id']:22s} {r['expect']:18s} {why}")
    graded = [x for x in rows if x["expect"] != "any"]
    passed = sum(x["verdict"] == "PASS" for x in graded)
    print(f"\n{passed}/{len(graded)} passed  ({raw['model']}, {raw['date']})")
    (HERE / "results" / "scores.json").write_text(json.dumps({"model": raw["model"], "date": raw["date"], "passed": passed, "of": len(graded), "rows": rows}, indent=2))
    return 0 if passed == len(graded) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
