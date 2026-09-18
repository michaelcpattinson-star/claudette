"""Build and query the passage index.

SQLite FTS5 with BM25 ranking and Porter stemming, from the standard library.
No embeddings, no model, no network: the index can be rebuilt by anyone from
the manifest, and a search result can be reproduced by hand with sqlite3.

Passage text is stored zlib-compressed and the FTS table is contentless
(index only), which roughly halves the file — it matters once the full tier
runs to nine thousand works. The cost is that FTS5's highlight/snippet
helpers are unavailable; `matched_terms` is computed in Python instead.

The refusal rule is here too, because it belongs with the retrieval rather
than with the prompt. A BM25 score is not comparable across queries, so the
rule is in terms of *coverage*: how many of the question's content terms the
best passage actually contains.
"""

from __future__ import annotations

import re
import sqlite3
import zlib
from pathlib import Path

from claudette.chunk import chunk
from claudette.envelope import Hit, ToolResponse
from claudette.manifest import Manifest, Work

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS works (
    slug TEXT PRIMARY KEY,
    gutenberg_id INTEGER,
    author TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    shelf TEXT NOT NULL,
    lane TEXT NOT NULL,
    language TEXT NOT NULL,
    curated INTEGER NOT NULL,
    source TEXT NOT NULL,
    why TEXT NOT NULL,
    passages INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS works_author ON works(author);
CREATE TABLE IF NOT EXISTS passages (
    id INTEGER PRIMARY KEY,
    slug TEXT NOT NULL REFERENCES works(slug),
    ordinal INTEGER NOT NULL,
    ztext BLOB NOT NULL,
    UNIQUE (slug, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    text, content='', tokenize='porter unicode61'
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

_WORD = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)


def content_terms(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in _WORD.findall(query.lower()):
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


def _z(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), 6)


def _unz(blob: bytes) -> str:
    return zlib.decompress(blob).decode("utf-8")


def build(
    manifest: Manifest,
    text_for: "callable",
    db_path: Path,
    *,
    tier: str = "core",
    skip_missing: bool = False,
    log=print,
) -> dict[str, int]:
    """(Re)build an index. `text_for(work)` returns the cleaned text or None.

    Returns passage counts per work. With `skip_missing`, a work whose text is
    unavailable is logged and left out rather than aborting the build — the
    full tier is nine thousand downloads and one failure should not cost the
    other 8,999.
    """
    tmp = db_path.with_suffix(".db.building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta VALUES ('tier', ?)", (tier,))
    con.execute("INSERT INTO meta VALUES ('schema', '2')")
    counts: dict[str, int] = {}
    n = 0
    for work in manifest.works:
        text = text_for(work)
        if text is None:
            if not skip_missing:
                raise FileNotFoundError(f"{work.slug}: no text. Run `claudette fetch` first.")
            log(f"  skip   {work.slug} (no text)")
            continue
        passages = chunk(text)
        con.execute(
            "INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                work.slug, work.id, work.author, work.title, work.year, work.shelf, work.lane,
                work.language, int(work.curated), work.source, work.why, len(passages),
            ),
        )
        cur = con.executemany(
            "INSERT INTO passages (slug, ordinal, ztext) VALUES (?,?,?)",
            [(work.slug, p.ordinal, _z(p.text)) for p in passages],
        )
        first = con.execute("SELECT MIN(id) FROM passages WHERE slug = ?", (work.slug,)).fetchone()[0]
        con.executemany(
            "INSERT INTO passages_fts (rowid, text) VALUES (?, ?)",
            [(first + p.ordinal, p.text) for p in passages],
        )
        counts[work.slug] = len(passages)
        n += 1
        if work.curated or n % 200 == 0:
            log(f"  {work.slug:44s} {len(passages):6d} passages" if work.curated else f"  … {n} works indexed")
        if n % 500 == 0:
            con.commit()
    con.execute("INSERT INTO passages_fts(passages_fts) VALUES ('optimize')")
    con.commit()
    con.close()
    db_path.unlink(missing_ok=True)
    tmp.replace(db_path)
    return counts


class Index:
    """Read-only access to a built index."""

    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(f"No index at {db_path}. Run `claudette fetch && claudette index`, or `claudette serve` to bootstrap.")
        self.path = db_path
        self.con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row

    # -- corpus ------------------------------------------------------------

    @property
    def tier(self) -> str:
        row = self.con.execute("SELECT value FROM meta WHERE key='tier'").fetchone()
        return row[0] if row else "core"

    def works(self, shelf: str | None = None, *, author: str | None = None, limit: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM works", []
        where = []
        if shelf:
            where.append("shelf = ?")
            args.append(shelf)
        if author:
            where.append("author LIKE ?")
            args.append(f"%{author}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY curated DESC, year, author, title"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return [dict(r) for r in self.con.execute(sql, args)]

    def authors(self, query: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT author, COUNT(*) AS works, SUM(passages) AS passages, MAX(curated) AS curated FROM works"
        args: list = []
        if query:
            sql += " WHERE author LIKE ?"
            args.append(f"%{query}%")
        sql += " GROUP BY author ORDER BY curated DESC, works DESC, author LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.con.execute(sql, args)]

    def stats(self) -> dict:
        w = self.con.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT author) a, SUM(passages) p, "
            "SUM(curated) c, COUNT(DISTINCT language) l FROM works"
        ).fetchone()
        return {"tier": self.tier, "works": w["n"], "authors": w["a"], "passages": w["p"], "curated_works": w["c"], "languages": w["l"]}

    # -- retrieval ---------------------------------------------------------

    def search(self, query: str, k: int = 8, *, slug: str | None = None, language: str | None = None, curated_only: bool = False) -> ToolResponse:
        terms = content_terms(query)
        if not terms:
            return ToolResponse.no_coverage("The question contains no searchable words once common words are removed.")
        sql = (
            "SELECT p.id, p.slug, p.ordinal, p.ztext, w.author, w.title, w.year, w.source, w.curated, "
            "bm25(passages_fts) AS score "
            "FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
            "JOIN works w ON w.slug = p.slug WHERE passages_fts MATCH ?"
        )
        args: list = [fts_query(terms)]
        if slug:
            sql += " AND p.slug = ?"
            args.append(slug)
        if language:
            sql += " AND w.language = ?"
            args.append(language)
        if curated_only:
            sql += " AND w.curated = 1"
        sql += " ORDER BY score LIMIT ?"
        args.append(k)
        rows = self.con.execute(sql, args).fetchall()
        if not rows:
            return ToolResponse.no_coverage(
                f"No passage in the corpus contains any of: {', '.join(terms)}. "
                "Most of this corpus predates 1927; try the older word for the idea."
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
            "SELECT ordinal, ztext FROM passages WHERE slug = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
            (slug, ordinal - context, ordinal + context),
        ).fetchall()
        if not any(r["ordinal"] == ordinal for r in rows):
            return ToolResponse.not_found(f"{slug} has passages 0–{w['passages'] - 1}; §{ordinal} does not exist.")
        return ToolResponse.ok(
            {
                "author": w["author"],
                "title": w["title"],
                "year": w["year"],
                "source": w["source"],
                "curated": bool(w["curated"]),
                "passages": [{"ref": f"{slug}§{r['ordinal']}", "ordinal": r["ordinal"], "text": _unz(r["ztext"])} for r in rows],
            }
        )

    def _hit(self, r: sqlite3.Row, terms: list[str]) -> Hit:
        text = _unz(r["ztext"])
        low = text.lower()
        matched = [t for t in terms if re.search(r"\b" + re.escape(_stem(t)) + r"\w*", low)]
        return Hit(
            ref=f"{r['slug']}§{r['ordinal']}",
            author=r["author"],
            title=r["title"],
            year=r["year"],
            ordinal=r["ordinal"],
            text=text,
            matched_terms=matched,
            source=r["source"],
            curated=bool(r["curated"]),
        )
