"""Run the eval questions through the chat client and record raw transcripts.

Needs the `chat` dependency group and Anthropic credentials. Writes
evals/results/raw_<model>.json; score.py grades it. Costs money — a dozen
questions at Opus prices is well under a pound, but it is not free.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent


def main(model: str = "claude-opus-5") -> int:
    from claudette.chat import ask

    qs = json.loads((HERE / "questions.json").read_text())["questions"]
    out = []
    for q in qs:
        print(f"[{q['id']}] {q['q']}", file=sys.stderr)
        turn = ask(q["q"], model=model)
        out.append(
            {
                **q,
                "answer": turn.answer,
                "tool_calls": turn.tool_calls,
                "statuses": turn.statuses,
                "retrieved_refs": sorted(turn.retrieved),
                "unverified_citations": turn.unverified_citations,
                "stop_reason": turn.stop_reason,
            }
        )
    dest = HERE / "results" / f"raw_{model}.json"
    dest.write_text(json.dumps({"model": model, "date": date.today().isoformat(), "results": out}, indent=2))
    print(f"wrote {dest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
