"""Универсальный индексер баз КонсультантПлюс.

Один проход по DT2: страницы группируются в документы (is_doc_start),
карточки из шапки первой страницы + FTS5-индекс полного текста (стемминг).
Тексты в БД не хранятся — только указатели страниц (dt2_off[]) для ленивой
сборки. Заголовки DT1 импортируются отдельной таблицей для обогащения.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, os.environ.get("CONSPLUS_HOME", str(Path(__file__).parent.parent)))
from consplus import Base, parse_text, parse_header
from consplus.requisites import parse_requisites, normalize_number
from consplus.paginate import is_doc_start

TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")

_stemmer = None


def stemmer():
    global _stemmer
    if _stemmer is None:
        try:
            import Stemmer
            _stemmer = Stemmer.Stemmer("russian")
        except ImportError:
            from nltk.stem.snowball import SnowballStemmer
            _stemmer = SnowballStemmer("russian")
    return _stemmer


def stem_text(text: str) -> str:
    st = stemmer()
    tokens = TOKEN_RE.findall(text.lower())
    if hasattr(st, "stemWords"):
        return " ".join(st.stemWords(tokens))
    return " ".join(st.stem(t) for t in tokens)


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = OFF;
PRAGMA busy_timeout = 30000;
CREATE TABLE IF NOT EXISTS docs(
    id INTEGER PRIMARY KEY,
    base TEXT NOT NULL,
    dt2_off INTEGER NOT NULL,
    pages TEXT NOT NULL DEFAULT '[]',
    agency TEXT, kind TEXT, date TEXT, number TEXT, title TEXT);
CREATE INDEX IF NOT EXISTS ix_docs_base ON docs(base);
CREATE INDEX IF NOT EXISTS ix_docs_req ON docs(base, kind, number, date);
CREATE TABLE IF NOT EXISTS headers(
    id INTEGER PRIMARY KEY,
    base TEXT NOT NULL,
    dt1_off INTEGER NOT NULL,
    kind TEXT, number TEXT, date TEXT, title TEXT, source TEXT, note TEXT);
CREATE INDEX IF NOT EXISTS ix_headers_req ON headers(base, kind, number, date);
"""


def ensure_schema(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    con.execute("SELECT 1 FROM sqlite_master WHERE name='fts_docs'")
    if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts_docs'").fetchone():
        con.execute("CREATE VIRTUAL TABLE fts_docs USING fts5(tokens, content='')")


def index_base(base_dir: str, db_path: str, limit: int | None = None,
               with_fts: bool = True, workers: int = 0):
    base = Base(base_dir)
    con = sqlite3.connect(db_path)
    ensure_schema(con)
    con.execute("DELETE FROM docs WHERE base=?", (base.name,))
    con.execute("DELETE FROM headers WHERE base=?", (base.name,))

    pool = Pool(workers or max(1, (os.cpu_count() or 4) - 1)) if with_fts else None

    t0 = time.time()
    n_pages = n_docs = 0
    batch_docs, batch_texts = [], []
    cur_pages: list[int] = []
    cur_text_parts: list[str] = []

    def close_doc():
        nonlocal cur_pages, cur_text_parts, n_docs
        if not cur_pages:
            return
        full_text = "\n".join(cur_text_parts)
        req = parse_requisites(cur_text_parts[0])
        batch_docs.append((base.name, cur_pages[0], json.dumps(cur_pages),
                           req["agency"], req["kind"], req["date"],
                           req["number"], req["title"]))
        batch_texts.append(full_text)
        n_docs += 1
        cur_pages = []
        cur_text_parts = []

    def flush():
        if not batch_docs:
            return
        tokens = pool.map(stem_text, batch_texts, chunksize=50) if pool else []
        _flush(con, batch_docs, tokens)
        batch_docs.clear()
        batch_texts.clear()

    for off, raw in base.iter_texts(limit):
        text = parse_text(raw)
        n_pages += 1
        if is_doc_start(text):
            close_doc()
        cur_pages.append(off)
        cur_text_parts.append(text)
        if len(batch_docs) >= 2000:
            flush()
            print(f"  {n_pages} pages -> {n_docs} docs, {time.time()-t0:.0f}s",
                  flush=True)
    close_doc()
    flush()
    if pool:
        pool.close()
    print(f"pages: {n_pages} -> documents: {n_docs} in {time.time()-t0:.0f}s",
          flush=True)

    # заголовки DT1 (обогащение)
    t0 = time.time()
    n = 0
    batch = []
    for off, raw in base.iter_headers(limit):
        fields = parse_header(raw)["raw_fields"]
        info = _header_info(fields)
        batch.append((base.name, off, info["kind"], info["number"],
                      info["date"], info["title"], info["source"], info["note"]))
        n += 1
        if len(batch) >= 10000:
            con.executemany("INSERT INTO headers(base,dt1_off,kind,number,date,title,source,note)"
                            " VALUES(?,?,?,?,?,?,?,?)", batch)
            con.commit()
            batch.clear()
    if batch:
        con.executemany("INSERT INTO headers(base,dt1_off,kind,number,date,title,source,note)"
                        " VALUES(?,?,?,?,?,?,?,?)", batch)
    con.commit()
    print(f"headers indexed: {n} in {time.time()-t0:.0f}s", flush=True)
    con.close()


def _flush(con, batch_docs, batch_fts):
    con.executemany(
        "INSERT INTO docs(base,dt2_off,pages,agency,kind,date,number,title)"
        " VALUES(?,?,?,?,?,?,?,?)", batch_docs)
    if batch_fts:
        maxid = con.execute("SELECT MAX(id) FROM docs").fetchone()[0]
        first = maxid - len(batch_docs) + 1
        con.executemany(
            "INSERT INTO fts_docs(rowid, tokens) VALUES(?,?)",
            [(first + i, tok) for i, tok in enumerate(batch_fts)])
    con.commit()


RE_HDR_DATE = re.compile(r"от (\d{2})\.(\d{2})\.(\d{4})")
RE_HDR_NUM = re.compile(r"N\s*([^\s\x00(]+)")


NOTE_MARKERS = ("утратил силу", "утрачивает силу", "вступил в силу",
                "вступает в силу", "не применяется", "признан утратившим",
                "признать утратившим", "новая редакция", "внесены изменения",
                "не подлежит применению")


def _header_info(fields: list[str]) -> dict:
    info = {"kind": fields[0] if fields else "", "title": "", "number": "",
            "date": None, "source": "", "note": ""}
    for f in fields[1:8]:
        m = RE_HDR_DATE.search(f)
        if m:
            info["title"] = f
            info["date"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            n = RE_HDR_NUM.search(f)
            if n:
                info["number"] = normalize_number(n.group(1))
            break
    for f in fields[2:9]:
        if f.startswith('"') and f != info["title"] and "," in f:
            info["source"] = f
            break
    # аннотация о статусе: любое поле с маркером статуса
    for f in fields[:6]:
        low = f.lower()
        if any(mk in low for mk in NOTE_MARKERS) and len(f) > 30:
            info["note"] = f
            break
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-fts", action="store_true")
    ap.add_argument("--workers", type=int, default=0)
    args = ap.parse_args()
    index_base(args.base_dir, args.db, args.limit, not args.no_fts, args.workers)


if __name__ == "__main__":
    main()
