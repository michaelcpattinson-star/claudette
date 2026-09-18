# Eval results

**Status: not yet run.** The harness exists and is wired (`run.py` → `score.py`),
but no run has been recorded. Numbers appear here only after someone runs it;
until then the honest claim is "the checks are mechanical and the questions are
public", not "Claudette passes".

To run (needs Anthropic credentials and the `chat` group):

    uv sync --group chat
    uv run python evals/run.py            # writes raw_claude-opus-5.json
    uv run python evals/score.py evals/results/raw_claude-opus-5.json
