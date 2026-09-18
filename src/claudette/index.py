"""Build and query the passage index.

SQLite FTS5 with BM25 ranking and Porter stemming, from the standard library.
No embeddings, no model, no network: the index can be rebuilt by anyone from
the manifest, and a search result can be reproduced by hand with sqlite3.

The refusal rule is here too, because it belongs with the retrieval rather
than with the prompt. A BM25 score is not comparable across queries, so the
rule is in terms of *coverage*: how many of the question's content terms the
best passage actually contains.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from claudette.chunk import chunk
from claudette.envelope import Hit, ToolResponse
from claudette.fetch import clean_path
from claudette.manifest import Manifest, Work

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    slug TEXT PRIMARY KEY,
    gutenberg_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    shelf TEXT NOT NULL,
    why TEXT NOT NULL,
    passages INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL REFERENCES works(slug),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (slug, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text,
    content='passages',
    content_rowid='id',
    tokenize='porter unicode61'
);
"""

# Words that carry no retrieval signal in questions. Short and unsurprising;
# the aim is only to stop "what does she think about" from counting as matches.
STOPWORDS = frozenset(
    """a an and are as at be by do does did for from had has have he her hers him his
    how i if in into is it its me my of on or our she so that the their them then
    there these they this those to us was we were what when where which who whom why
    will with would you your about can could should say says said think thinks
    tell told mean means like just also any some very more most much than""".split()
)

_WORD = re.compile(r"[a-z0-9']+")


def content_terms(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in _WORD.findall(query.lower()):
        w = w.strip("'")
        if len(w) > 1 and w not in STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


def _stem(t: str) -> str:
    """A crude stem for reporting which query words a passage contains.

    FTS5 does the real stemming for ranking; this only has to agree with it
    often enough that `matched_terms` is honest. 'masters' → 'master',
    'commanding' → 'command', 'sympathies' → 'sympath'.
    """
    if len(t) > 5:
        t = re.sub(r"(ies|ing|ed|es|s)$", "", t)
    elif len(t) > 3:
        t = re.sub(r"s$", "", t)
    return t


def fts_query(terms: list[str]) -> str:
    # OR-join so BM25 ranks by how many and how rare; quoting defends against
    # FTS5 syntax characters in user input.
    return " OR ".join('"' + t.replace('"', "") + '"' for t in terms)


def build(manifest: Manifest, texts_dir: Path, db_path: Path, *, log=print) -> dict[str, int]:
    """(Re)build the index from cleaned texts. Returns passage counts per work."""
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    counts: dict[str, int] = {}
    for work in manifest.works:
        path = clean_path(texts_dir, work)
        if not path.exists():
            raise FileNotFoundError(f"{work.slug}: not fetched ({path}). Run `claudette fetch` first.")
        passages = chunk(path.read_text(encoding="utf-8"))
        con.execute(
            "INSERT INTO works VALUES (?,?,?,?,?,?,?,?)",
            (work.slug, work.id, work.author, work.title, work.year, work.shelf, work.why, len(passages)),
        )
        con.executemany(
            "INSERT INTO passages (slug, ordinal, text) VALUES (?,?,?)",
            [(work.slug, p.ordinal, p.text) for p in passages],
        )
        counts[work.slug] = len(passages)
        log(f"  {work.slug:36s} {len(passages):6d} passages")
    con.execute("INSERT INTO passages_fts(passages_fts) VALUES ('rebuild')")
    con.commit()
    con.close()
    return counts


class Index:
    """Read-only access to a built index."""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(f"No index at {db_path}. Run `claudette fetch && claudette index`, or `claudette serve` to bootstrap.")
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    # -- corpus ------------------------------------------------------------

    def works(self, shelf: str | None = None) -> list[dict]:
        sql = "SELECT * FROM works"
        args: tuple = ()
        if shelf:
            sql += " WHERE shelf = ?"
            args = (shelf,)
        return [dict(r) for r in self.con.execute(sql + " ORDER BY year", args)]

    def stats(self) -> dict:
        w = self.con.execute("SELECT COUNT(*) n, COUNT(DISTINCT author) a, SUM(passages) p FROM works").fetchone()
        return {"works": w["n"], "authors": w["a"], "passages": w["p"]}

    # -- retrieval ---------------------------------------------------------

    def search(self, query: str, k: int = 8, *, slug: str | None = None) -> ToolResponse:
        terms = content_terms(query)
        if not terms:
            return ToolResponse.no_coverage("The question contains no searchable words once common words are removed.")
        sql = (
            "SELECT p.id, p.slug, p.ordinal, p.text, w.author, w.title, w.year, "
            "bm25(passages_fts) AS score "
            "FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
            "JOIN works w ON w.slug = p.slug WHERE passages_fts MATCH ?"
        )
        args: list = [fts_query(terms)]
        if slug:
            sql += " AND p.slug = ?"
            args.append(slug)
        sql += " ORDER BY score LIMIT ?"
        args.append(k)
        rows = self.con.execute(sql, args).fetchall()
        if not rows:
            return ToolResponse.no_coverage(
                f"No passage in the corpus contains any of: {', '.join(terms)}. "
                "The women in this corpus wrote between 1694 and 1922; try the older word for the idea."
            )
        hits = [self._hit(r, terms) for r in rows]
        best = max(len(h.matched_terms) for h in hits)
        # One- and two-word questions need every word; longer ones, at least half.
        # "Elon Musk" must not count as covered because a garden has musk in it.
        needed = len(terms) if len(terms) <= 2 else (len(terms) + 1) // 2
        if best < needed:
            return ToolResponse.weak(
                [h.model_dump() for h in hits],
                f"Thin match: the best passage contains only {best} of {len(terms)} question terms "
                f"({', '.join(terms)}). Use it only if it genuinely bears on the question, and say the match is partial.",
            )
        return ToolResponse.ok([h.model_dump() for h in hits])

    def read(self, ref: str, context: int = 1) -> ToolResponse:
        """A passage by citation key, with `context` neighbours either side."""
        try:
            slug, ord_s = ref.replace("#", "§").split("§")
            ordinal = int(ord_s)
        except ValueError:
            return ToolResponse.not_found(f"Reference {ref!r} is not of the form 'slug§n'.")
        w = self.con.execute("SELECT * FROM works WHERE slug = ?", (slug,)).fetchone()
        if w is None:
            return ToolResponse.not_found(f"No work with slug {slug!r} in the corpus.")
        rows = self.con.execute(
            "SELECT ordinal, text FROM passages WHERE slug = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
            (slug, ordinal - context, ordinal + context),
        ).fetchall()
        if not any(r["ordinal"] == ordinal for r in rows):
            return ToolResponse.not_found(f"{slug} has passages 0–{w['passages'] - 1}; §{ordinal} does not exist.")
        return ToolResponse.ok(
            {
                "author": w["author"],
                "title": w["title"],
                "year": w["year"],
                "passages": [{"ref": f"{slug}§{r['ordinal']}", "ordinal": r["ordinal"], "text": r["text"]} for r in rows],
            }
        )

    def _hit(self, r: sqlite3.Row, terms: list[str]) -> Hit:
        low = r["text"].lower()
        matched = [t for t in terms if re.search(r"\b" + re.escape(_stem(t)) + r"\w*", low)]
        return Hit(
            ref=f"{r['slug']}§{r['ordinal']}",
            author=r["author"],
            title=r["title"],
            year=r["year"],
            ordinal=r["ordinal"],
            text=r["text"],
            matched_terms=matched,
        )
