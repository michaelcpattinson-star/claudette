import pytest

from claudette.chunk import HARD_MAX, TARGET, chunk, paragraphs
from claudette.fetch import header_title, strip_boilerplate
from claudette.manifest import Work
from tests.conftest import FIXTURE_TEXT, FIXTURE_WORKS, GUTENBERG_WRAP


def _raw(slug: str) -> tuple[Work, str]:
    w = next(w for w in FIXTURE_WORKS if w.slug == slug)
    return w, GUTENBERG_WRAP.format(title=w.title, upper=w.title.upper(), body=FIXTURE_TEXT[slug])


def test_boilerplate_and_licence_are_stripped():
    w, raw = _raw("fixture-mill")
    text = strip_boilerplate(raw, w)
    assert "Project Gutenberg" not in text
    assert "Licence text" not in text
    assert text.startswith("CHAPTER ONE")


def test_start_after_drops_the_foreign_introduction():
    w, raw = _raw("fixture-committee")
    text = strip_boilerplate(raw, w)
    assert "zebra" not in text
    assert "another hand" not in text
    assert text.lstrip().startswith("The committee does not vote.")


def test_missing_trim_marker_is_an_error_not_a_silence():
    w = FIXTURE_WORKS[0].model_copy(update={"start_after": "NO SUCH MARKER"})
    _, raw = _raw("fixture-committee")
    with pytest.raises(ValueError, match="start_after marker not found"):
        strip_boilerplate(raw, w)


def test_header_title_is_read_from_first_line():
    _, raw = _raw("fixture-mill")
    assert header_title(raw) == "The Mill at Dawn"


def test_paragraphs_join_wrapped_lines():
    assert paragraphs("a line\nwrapped here\n\nsecond") == ["a line wrapped here", "second"]


def test_chunk_merges_short_paragraphs_and_splits_long_ones():
    w, raw = _raw("fixture-mill")
    ps = chunk(strip_boilerplate(raw, w))
    assert [p.ordinal for p in ps] == list(range(len(ps)))
    assert all(len(p.text) <= HARD_MAX for p in ps)
    long_pieces = [p for p in ps if "long chapter" in p.text]
    assert len(long_pieces) >= 2, "a 3000-char paragraph must be split at sentence ends"
    assert all(p.text.rstrip().endswith(".") for p in long_pieces)
    assert any(len(p.text) > TARGET / 2 for p in ps)


def test_transcriber_credits_at_top_are_dropped():
    from claudette.fetch import _drop_transcriber_credits

    real = "She transcribed the letter " + "and thought about it for a long while. " * 12
    text = f"Produced by A. Volunteer and the Online\nDistributed Proofreading Team.\n\n[Transcriber's note: kept spelling.]\n\nTHE WORK\n\n{real}"
    out = _drop_transcriber_credits(text)
    assert out.lstrip().startswith("THE WORK")
    assert real in out  # a long real paragraph survives even though it says "transcribed"
