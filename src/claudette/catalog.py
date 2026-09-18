"""Build the full catalogue: every Gutenberg text by women, per Wikidata.

Two inputs, both reproducible by anyone:

  1. Wikidata. Every item with a Project Gutenberg author ID (P1938) whose
     "sex or gender" (P21) is female (Q6581072). The SPARQL query is
     `WIKIDATA_QUERY` below; the result ships as data/authors.csv with the
     Wikidata QID on every row, so any entry can be checked in one click.

  2. Gutenberg's own RDF metadata dump. A text qualifies when its `dcterms:type`
     is Text and EVERY agent in an authorial role — creator, editor,
     translator, contributor, compiler — is on the Wikidata list. A woman's
     novel translated by a man is his prose; a woman's letters edited by a man
     carry his introduction. Illustrators are not prose and are ignored.

The output, data/works.full.csv, is what `claudette expand` downloads. It is
generated, not curated: its `why` is the Wikidata basis, its shelf is
"uncurated", and its front matter has not been checked by a person. The
curated manifest remains the reviewed core.
"""

from __future__ import annotations

import csv
import io
import re
import tarfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

WIKIDATA_QUERY = """\
SELECT ?a ?pg ?aLabel ?born ?died WHERE {
  ?a wdt:P1938 ?pg ; wdt:P21 wd:Q6581072 .
  OPTIONAL { ?a wdt:P569 ?born } OPTIONAL { ?a wdt:P570 ?died }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,fr,de,es,it,nl,sv,pt,fi,[AUTO_LANGUAGE]". }
}"""

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dcterms": "http://purl.org/dc/terms/",
    "pgterms": "http://www.gutenberg.org/2009/pgterms/",
    "marcrel": "http://id.loc.gov/vocabulary/relators/",
}
# Roles whose holder must be a woman for the text to qualify.
AUTHORIAL_ROLES = ("{%s}creator" % NS["dcterms"],) + tuple(
    "{%s}%s" % (NS["marcrel"], r) for r in ("aut", "edt", "trl", "ctb", "com", "cmp")
)
_AGENT_ID = re.compile(r"agents/(\d+)$")
_EBOOK_ID = re.compile(r"ebooks/(\d+)$")


@dataclass
class Author:
    agent_id: int
    qid: str
    name: str
    born: int | None
    died: int | None


@dataclass
class FullWork:
    id: int
    slug: str
    authors: str  # "; "-joined
    title: str
    language: str
    year: int | None  # author's birth year + 30 as a rough era when nothing better exists; None if unknown
    basis: str  # the Wikidata QIDs that qualify it


def load_authors(path: Path) -> dict[int, Author]:
    out: dict[int, Author] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                agent = int(row["pg"])
            except ValueError:
                continue
            out[agent] = Author(
                agent_id=agent,
                qid=row["a"].rsplit("/", 1)[-1],
                name=row["aLabel"],
                born=_year(row.get("born")),
                died=_year(row.get("died")),
            )
    return out


def _year(s: str | None) -> int | None:
    if not s:
        return None
    m = re.match(r"(-?\d{1,4})-", s)
    return int(m.group(1)) if m else None


def slugify(s: str, limit: int = 40) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:limit].rstrip("-")


def parse_ebook(xml_bytes: bytes, women: dict[int, Author]) -> FullWork | None:
    root = ET.fromstring(xml_bytes)
    ebook = root.find("pgterms:ebook", NS)
    if ebook is None:
        return None
    m = _EBOOK_ID.search(ebook.get("{%s}about" % NS["rdf"], ""))
    if not m:
        return None
    ebook_id = int(m.group(1))

    types = [v.text for v in ebook.findall("dcterms:type/rdf:Description/rdf:value", NS)]
    if "Text" not in types:
        return None

    agents: list[int] = []
    for role in AUTHORIAL_ROLES:
        for el in ebook.findall(role):
            ag = el.find("pgterms:agent", NS)
            about = ag.get("{%s}about" % NS["rdf"], "") if ag is not None else el.get("{%s}resource" % NS["rdf"], "")
            am = _AGENT_ID.search(about)
            if am:
                agents.append(int(am.group(1)))
    creators = ebook.findall("dcterms:creator", NS)
    if not creators or not agents:
        return None  # anonymous or unattributed: no author, no entry
    if any(a not in women for a in agents):
        return None

    title = " ".join((ebook.findtext("dcterms:title", default="", namespaces=NS) or "").split())
    lang = ebook.findtext("dcterms:language/rdf:Description/rdf:value", default="", namespaces=NS) or ""
    names = sorted({women[a].name for a in agents})
    qids = sorted({women[a].qid for a in agents})
    born = [women[a].born for a in agents if women[a].born]
    year = (min(born) + 30) if born else None
    surname = slugify(names[0].split()[-1]) if names else "anon"
    slug = f"{surname}-{slugify(title, 30)}-{ebook_id}".replace("--", "-")
    return FullWork(ebook_id, slug, "; ".join(names), title, lang, year, ",".join(qids))


def build_catalog(rdf_tar: Path, authors_csv: Path, *, log=print) -> list[FullWork]:
    women = load_authors(authors_csv)
    works: list[FullWork] = []
    seen = 0
    with tarfile.open(rdf_tar, "r:bz2") as tar:
        for member in tar:
            if not member.name.endswith(".rdf"):
                continue
            seen += 1
            f = tar.extractfile(member)
            if f is None:
                continue
            try:
                w = parse_ebook(f.read(), women)
            except ET.ParseError:
                continue
            if w:
                works.append(w)
            if seen % 10000 == 0:
                log(f"  scanned {seen} records, {len(works)} qualify")
    works.sort(key=lambda w: w.id)
    log(f"  scanned {seen} records; {len(works)} texts by {len({q for w in works for q in w.basis.split(',')})} women qualify")
    return works


def write_catalog(works: list[FullWork], dest: Path, *, generated: str) -> None:
    with open(dest, "w", encoding="utf-8", newline="") as f:
        f.write(f"# generated {generated}; see claudette.catalog for the rule. Do not hand-edit.\n")
        wr = csv.writer(f)
        wr.writerow(["id", "slug", "authors", "title", "language", "year", "basis"])
        for w in works:
            wr.writerow([w.id, w.slug, w.authors, w.title, w.language, w.year or "", w.basis])


def read_catalog(path: Path) -> list[FullWork]:
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        lines = [l for l in f if not l.startswith("#")]
    for row in csv.DictReader(io.StringIO("".join(lines))):
        out.append(FullWork(int(row["id"]), row["slug"], row["authors"], row["title"], row["language"], int(row["year"]) if row["year"] else None, row["basis"]))
    return out
