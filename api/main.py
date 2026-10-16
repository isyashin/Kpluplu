"""Search API поверх оригинальной базы КонсультантПлюс.

Архитектура хранения:
  index.db        — объединённые docs/headers/relations (глобальные id + колонка part, local_id)
  index_part1..3.db — рабочие FTS5-индексы (contentless) по группам баз
Поиск: FTS-запрос в каждой части → слияние по рангу → карточки из index.db.
Тексты: ленивая сборка страниц из оригинальных DT2 по указателям pages[].
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse

ROOT = Path(__file__).parent.parent
INDEX_DB = Path(os.environ.get("CONSPLUS_INDEX", ROOT / "index.db"))
PARTS_DIR = Path(os.environ.get("CONSPLUS_PARTS_DIR", INDEX_DB.parent))
BASES_DIR = Path(os.environ.get(
    "CONSPLUS_BASES", ROOT / "BASE"))
PARTS = [
    (1, PARTS_DIR / "index_part1.db", 0),
    (2, PARTS_DIR / "index_part2.db", 5789815),
    (3, PARTS_DIR / "index_part3.db", 14573240),
]

import sys
sys.path.insert(0, str(ROOT))
from consplus import read_record_at, parse_text  # noqa: E402

app = FastAPI(title="ConsultantPlus Direct API", version="0.3.0")

_stemmer = None
_base_part: dict[str, int] = {}
_base_count: dict[str, int] = {}
TINY_BASE_LIMIT = 500
TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")


def stemmer():
    global _stemmer
    if _stemmer is None:
        import Stemmer
        _stemmer = Stemmer.Stemmer("russian")
    return _stemmer


def base_part_map() -> dict[str, int]:
    """base -> номер части (1..3), в которой лежит её FTS."""
    global _base_part, _base_count
    if not _base_part:
        with db() as con:
            _base_part = {r[0]: r[1] for r in con.execute(
                "SELECT DISTINCT base, part FROM docs")}
            _base_count = {r[0]: r[1] for r in con.execute(
                "SELECT base, COUNT(*) FROM docs GROUP BY base")}
    return _base_part


def base_count(base: str) -> int:
    base_part_map()
    return _base_count.get(base, 0)


def search_parts() -> list[tuple[int, Path, int]]:
    """Use offsets stored by the merge; old indexes use their known offsets."""
    with db() as con:
        try:
            offsets = dict(con.execute("SELECT part, offset FROM part_offsets"))
        except sqlite3.OperationalError:
            offsets = {}
    return [(part, path, offsets.get(part, fallback))
            for part, path, fallback in PARTS]


@contextmanager
def db():
    con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def stem_query(q: str) -> str:
    words = [w for w in TOKEN_RE.findall(q.lower()) if len(w) > 2]
    if not words:
        return '""'
    tokens = stemmer().stemWords(words)
    return " AND ".join(tokens)


def fetch_text(base: str, dt2_off: int, pages: str | None = None) -> str | None:
    mm = _base_mmap(base)
    if mm is None:
        return None
    offs = [dt2_off]
    if pages:
        try:
            parsed = json.loads(pages)
            if parsed:
                offs = parsed
        except Exception:
            pass
    parts = []
    for off in offs:
        if off + 8 > len(mm):
            continue
        ln = int.from_bytes(mm[off + 2:off + 6], "little")
        comp = ln - 10
        if not (10 < comp < 4_000_000) or off + 8 + comp > len(mm):
            continue
        import zlib as _zlib
        d = _zlib.decompressobj(15)
        try:
            raw = d.decompress(mm[off + 8:off + 8 + comp + 64])
        except _zlib.error:
            continue
        if d.eof and raw:
            parts.append(parse_text(raw))
    return "\n".join(parts) if parts else None


_mmaps: dict[str, object] = {}


def _base_mmap(base: str):
    """Открытый mmap на DT2 базы (разделяемый, read-only)."""
    import mmap as _mmap
    m = _mmaps.get(base)
    if m is None:
        path = BASES_DIR / base / f"{base}.DT2"
        if not path.exists():
            return None
        f = open(path, "rb")
        m = _mmap.mmap(f.fileno(), 0, access=_mmap.ACCESS_READ)
        _mmaps[base] = m
    return m


def make_snippet(text: str, query: str, width: int = 300) -> str:
    words = [w for w in TOKEN_RE.findall(query.lower()) if len(w) > 2]
    low = text.lower()
    pos = -1
    for w in words:
        pos = low.find(w)
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].strip()
    start = max(0, pos - width // 3)
    snippet = text[start:start + width].strip()
    for w in words:
        snippet = re.sub(f"(?i)({re.escape(w)}\\w*)", r"**\1**", snippet)
    return ("…" if start else "") + snippet + "…"


def looks_full_document(text: str) -> bool:
    lines = [l.strip() for l in text.split("\n") if l.strip()][:4]
    if not lines:
        return False
    head = " ".join(lines)
    return bool(re.search(r"от \d{1,2} [а-яё]+ \d{4}|от \d{2}\.\d{2}\.\d{4}", head))


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/stats")
def stats():
    with db() as con:
        return {
            "docs": con.execute("SELECT COUNT(*) FROM docs").fetchone()[0],
            "headers": con.execute("SELECT COUNT(*) FROM headers").fetchone()[0],
            "relations": con.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
            "bases": [r[0] for r in con.execute(
                "SELECT DISTINCT base FROM docs ORDER BY base")],
        }


@app.get("/api/bases")
def bases():
    with db() as con:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT base FROM docs ORDER BY base")]


@app.get("/api/search")
def search(
    q: str,
    base: str | None = None,
    kind: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
):
    fts_q = stem_query(q)
    # фильтры по документам (одинаковы для всех частей)
    conds, fparams = [], []
    if base:
        conds.append("d.base = ?")
        fparams.append(base)
    if kind:
        conds.append("d.kind = ?")
        fparams.append(kind.upper())
    if date_from:
        conds.append("d.date >= ?")
        fparams.append(date_from)
    if date_to:
        conds.append("d.date <= ?")
        fparams.append(date_to)
    extra = (" AND " + " AND ".join(conds)) if conds else ""

    want = page * per_page
    from concurrent.futures import ThreadPoolExecutor

    # крошечные базы: FTS-скан части дольше, чем чтение их первых страниц
    if base and base_count(base) < TINY_BASE_LIMIT:
        words = [w for w in TOKEN_RE.findall(q.lower()) if len(w) > 2]
        tiny_sql = ("SELECT id, base, kind, number, date, title, agency, dt2_off "
                    "FROM docs WHERE base=?")
        tiny_params = [base]
        if kind:
            tiny_sql += " AND kind=?"
            tiny_params.append(kind.upper())
        if date_from:
            tiny_sql += " AND date>=?"
            tiny_params.append(date_from)
        if date_to:
            tiny_sql += " AND date<=?"
            tiny_params.append(date_to)
        tiny_sql += " ORDER BY id DESC"
        with db() as con:
            rows = [dict(r) for r in con.execute(
                tiny_sql, tiny_params)]
        matched = []
        for r in rows:
            # только первая страница: там заголовок/аннотация документа
            text = fetch_text(r["base"], r.pop("dt2_off"), None) or ""
            low = text.lower()
            if all(w in low for w in words):
                r["fragment"] = not (text and looks_full_document(text))
                r["snippet"] = make_snippet(text, q)
                matched.append(r)
        start = (page - 1) * per_page
        return {"page": page, "items": matched[start:start + per_page]}

    # если задана base — её FTS живёт ровно в одной части
    parts = search_parts()
    if base:
        p = base_part_map().get(base)
        if p:
            parts = [x for x in parts if x[0] == p]

    def query_part(part):
        part_no, part_path, offset = part
        out = []
        if not part_path.exists():
            raise HTTPException(503, f"FTS index part is missing: {part_path}")
        pcon = sqlite3.connect(f"file:{part_path}?mode=ro", uri=True, timeout=60)
        try:
            sql = f"""SELECT f.rowid, f.rank
                      FROM fts_docs f JOIN docs d ON d.id = f.rowid
                      WHERE fts_docs MATCH ?{extra}
                      ORDER BY f.rank, f.rowid LIMIT ?"""
            for rowid, rank in pcon.execute(sql, [fts_q, *fparams, want]):
                out.append((rank, rowid + offset))
        except sqlite3.Error as exc:
            raise HTTPException(503, f"FTS query failed in part {part_no}: {exc}") from exc
        finally:
            pcon.close()
        return out

    with ThreadPoolExecutor(max_workers=len(parts)) as ex:
        results = ex.map(query_part, parts)
    candidates: list[tuple[float, int]] = [c for r in results for c in r]
    candidates.sort(key=lambda x: (x[0], x[1]))
    ids = [gid for _, gid in candidates[(page - 1) * per_page: page * per_page]]
    if not ids:
        return {"page": page, "items": []}

    with db() as con:
        marks = ",".join("?" * len(ids))
        rows = {r["id"]: dict(r) for r in con.execute(
            f"""SELECT id, base, kind, number, date, title, agency, dt2_off, pages
                FROM docs WHERE id IN ({marks})""", ids)}
    items = []
    for gid in ids:
        r = rows.get(gid)
        if not r:
            continue
        text = fetch_text(r["base"], r.pop("dt2_off"), r.pop("pages", None))
        r["fragment"] = not (text and looks_full_document(text))
        r["snippet"] = make_snippet(text, q) if text else ""
        items.append(r)
    return {"page": page, "items": items}


@app.get("/api/documents/requisites")
def by_requisites(number: str, date: str | None = None, base: str | None = None):
    sql = ("SELECT id, base, kind, number, date, title, agency, dt2_off "
           "FROM docs WHERE number = ?")
    params: list = [number]
    if date:
        sql += " AND date = ?"
        params.append(date)
    if base:
        sql += " AND base = ?"
        params.append(base)
    sql += " ORDER BY (date IS NOT NULL) DESC, date DESC LIMIT 50"
    with db() as con:
        rows = [dict(r) for r in con.execute(sql, params)]
    for row in rows:
        text = fetch_text(row["base"], row.pop("dt2_off"), None)
        row["preview"] = " ".join(text.split())[:200] if text else ""
        head = row["preview"][:120].casefold()
        record_date = row["date"]
        date_in_text = (f"{record_date[8:10]}.{record_date[5:7]}.{record_date[:4]}"
                        if record_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", record_date)
                        else "")
        row["header_match"] = bool(
            date_in_text and date_in_text in head and number.casefold() in head
            and not head.startswith(("обзор изменений", "архивный обзор изменений"))
        )
    rows.sort(key=lambda row: not row["header_match"])
    return {"items": rows}


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    with db() as con:
        row = con.execute(
            "SELECT id, base, dt2_off, agency, kind, number, date, title"
            " FROM docs WHERE id=?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "document not found")
        doc = dict(row)
        hdr = con.execute(
            """SELECT title, source, note FROM headers
               WHERE base=? AND number=? AND date IS ? LIMIT 1""",
            (doc["base"], doc["number"].lower(), doc["date"])).fetchone()
        if hdr:
            doc["header_title"] = hdr["title"]
            doc["source"] = hdr["source"]
            doc["note"] = hdr["note"]
        editions = []
        if doc["date"]:
            editions = [dict(r) for r in con.execute(
                "SELECT id, date, title FROM docs WHERE base=? AND number=? AND date=?"
                " AND id != ? ORDER BY id",
                (doc["base"], doc["number"], doc["date"], doc_id))]
        doc["editions"] = editions
        doc.pop("dt2_off")
    return doc


@app.get("/api/documents/{doc_id}/text", response_class=PlainTextResponse)
def document_text(doc_id: int):
    with db() as con:
        row = con.execute("SELECT base, dt2_off, pages FROM docs WHERE id=?",
                          (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "document not found")
    text = fetch_text(row["base"], row["dt2_off"], row["pages"])
    if text is None:
        raise HTTPException(404, "text not available")
    return text


@app.get("/api/documents/{doc_id}/status")
def document_status(doc_id: int):
    with db() as con:
        row = con.execute("SELECT base, number, date FROM docs WHERE id=?",
                          (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "document not found")
        hdr = con.execute(
            "SELECT note FROM headers WHERE base=? AND number=? AND date IS ? LIMIT 1",
            (row["base"], row["number"].lower(), row["date"])).fetchone()
        note = hdr["note"] if hdr else None
    status = "unknown"
    if note:
        low = note.lower()
        if "фактически утратил силу" in low:
            status = "factually_obsolete"
        elif "утратил силу" in low or "признан утратившим" in low:
            status = "repealed"
        elif "утрачивает силу" in low:
            status = "to_be_repealed"
        elif "внесены изменения" in low:
            status = "amended"
        elif "вступил в силу" in low or "вступает в силу" in low:
            status = "in_force"
    return {"id": doc_id, "status": status, "note": note,
            "basis": "header_note" if note else None}


@app.get("/api/documents/{doc_id}/relations")
def document_relations(doc_id: int):
    with db() as con:
        row = con.execute("SELECT base, number, date, title FROM docs WHERE id=?",
                          (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "document not found")
        base, number, date, title = row["base"], row["number"], row["date"], row["title"]
        if not number:
            return {"doc": title, "outgoing": [], "incoming": [],
                    "basis": "parsed_header_notes"}
        note_row = con.execute(
            "SELECT note FROM headers WHERE base=? AND number=? AND date IS ? LIMIT 1",
            (base, number.lower(), date)).fetchone()
        source_note = note_row["note"] if note_row else None
        outgoing = [dict(r) for r in con.execute(
            """SELECT DISTINCT relation, target_number, target_date, target_raw
               FROM relations WHERE base=? AND src_number=? AND src_date IS ?
               AND target_number IS NOT NULL AND target_number != ''
               AND target_date IS NOT NULL AND target_date != ''""",
            (base, number.lower(), date))]
        incoming = [dict(r) for r in con.execute(
            """SELECT DISTINCT relation, src_number, src_date, src_title
               FROM relations WHERE base=? AND target_number=? AND target_date IS ?
               AND src_number IS NOT NULL AND src_number != ''""",
            (base, number.lower(), date))]
        for r in outgoing:
            r["source_note"] = source_note
            tgt = con.execute(
                "SELECT id, title FROM docs WHERE base=? AND number=? AND date=? LIMIT 2",
                (base, r["target_number"], r["target_date"])).fetchall()
            r["target_docs"] = [dict(t) for t in tgt]
        for r in incoming:
            hdr = con.execute(
                "SELECT note FROM headers WHERE base=? AND number=? AND date IS ? LIMIT 1",
                (base, r["src_number"], r["src_date"])).fetchone()
            r["source_note"] = hdr["note"] if hdr else None
            src = con.execute(
                "SELECT id, title FROM docs WHERE base=? AND number=? AND date=? LIMIT 2",
                (base, r["src_number"], r["src_date"])).fetchall()
            r["src_docs"] = [dict(s) for s in src]
    return {"doc": title, "outgoing": outgoing, "incoming": incoming,
            "basis": "parsed_header_notes"}
