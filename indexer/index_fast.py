"""Быстрый индексер: неблокирующий конвейер.

Главный поток: сканирует DT2 на смещения чанков (regex по mmap), группирует
страницы в документы, пишет в SQLite.
Воркеры (Pool): читают записи, распаковывают zlib, разбирают реквизиты,
стеммят текст — всё параллельно.

Группировка сохраняет порядок: pool.imap (не unordered).
"""
import argparse
import json
import mmap
import os
import re
import sqlite3
import sys
import time
import zlib
from multiprocessing import Pool

sys.path.insert(0, os.environ.get("CONSPLUS_HOME", str(Path(__file__).parent.parent)))
from consplus import Base, parse_header
from consplus.requisites import parse_requisites, normalize_number
from consplus.paginate import is_doc_start

CHUNK_RE = re.compile(rb"\x75\x01(....)..\x78\x9c", re.DOTALL)
TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")

# --- worker side ---
_W = {}


def _init_worker(path):
    f = open(path, "rb")
    _W["mm"] = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    _W["f"] = f
    try:
        import Stemmer
        _W["stemmer"] = Stemmer.Stemmer("russian")
    except ImportError:
        from nltk.stem.snowball import SnowballStemmer
        _W["stemmer"] = SnowballStemmer("russian")


def _stem(text: str) -> str:
    st = _W["stemmer"]
    tokens = TOKEN_RE.findall(text.lower())
    if hasattr(st, "stemWords"):
        return " ".join(st.stemWords(tokens))
    return " ".join(st.stem(t) for t in tokens)


def _parse_text(raw: bytes) -> str:
    s = raw.decode("cp866", "replace").strip("\x00")
    s = re.sub(r"\x00+", "\n", s)
    s = re.sub(r"[\x01-\x08\x0b-\x1f]", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    if "-" * 70 in s:
        s = s.split("-" * 70)[0]
    return s.strip()


def process_batch(offsets):
    """Для списка смещений: (off, is_start, agency, kind, date, number, title, tokens).

    Текст НЕ возвращаем (не нужен главному) — экономия IPC ~100 КБ/стр.
    """
    mm = _W["mm"]
    out = []
    for off in offsets:
        try:
            ln = int.from_bytes(mm[off + 2:off + 6], "little")
            comp = ln - 10
            if not (10 < comp < 4_000_000):
                continue
            d = zlib.decompressobj(15)
            raw = d.decompress(mm[off + 8:off + 8 + comp + 64])
            if not d.eof or len(raw) < 4:
                continue
            text = _parse_text(raw)
            start = is_doc_start(text)
            req = parse_requisites(text)
            tokens = _stem(text)
            out.append((off, start, req["agency"], req["kind"], req["date"],
                        req["number"], req["title"], tokens))
        except Exception:
            continue
    return out


# --- main side ---
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

RE_HDR_DATE = re.compile(r"от (\d{2})\.(\d{2})\.(\d{4})")
RE_HDR_NUM = re.compile(r"N\s*([^\s\x00(]+)")
NOTE_MARKERS = ("утратил силу", "утрачивает силу", "вступил в силу",
                "вступает в силу", "не применяется", "признан утратившим",
                "признать утратившим", "новая редакция", "внесены изменения",
                "не подлежит применению")


def _header_info(fields):
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
    for f in fields[:6]:
        low = f.lower()
        if any(mk in low for mk in NOTE_MARKERS) and len(f) > 30:
            info["note"] = f
            break
    return info


def scan_offsets(path: str, limit: int | None = None):
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        end = limit if limit else len(mm)
        for m in CHUNK_RE.finditer(mm, 0, end):
            yield m.start()
        mm.close()


def batched(it, n):
    b = []
    for x in it:
        b.append(x)
        if len(b) >= n:
            yield b
            b = []
    if b:
        yield b


def index_base_fast(base_dir: str, db_path: str, limit: int | None = None,
                    workers: int = 4, batch: int = 2000):
    base = Base(base_dir)
    con = sqlite3.connect(db_path, timeout=60)
    con.executescript(SCHEMA)
    if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='fts_docs'").fetchone():
        con.execute("CREATE VIRTUAL TABLE fts_docs USING fts5(tokens, content='')")
    con.execute("DELETE FROM docs WHERE base=?", (base.name,))
    con.execute("DELETE FROM headers WHERE base=?", (base.name,))
    con.commit()

    dt2 = str(base.dir / f"{base.name}.DT2")
    t0 = time.time()
    n_pages = n_docs = 0
    cur_pages: list[int] = []
    cur_req: tuple | None = None
    cur_tokens: list[str] = []
    pend_docs, pend_tokens = [], []

    def close_doc():
        nonlocal cur_pages, cur_req, cur_tokens, n_docs
        if not cur_pages:
            return
        agency, kind, date, number, title = cur_req if cur_req else ("", "", None, "", "")
        pend_docs.append((base.name, cur_pages[0], json.dumps(cur_pages),
                          agency, kind, date, number, title))
        pend_tokens.append(" ".join(cur_tokens))
        n_docs += 1
        cur_pages, cur_req, cur_tokens = [], None, []

    def flush():
        if not pend_docs:
            return
        con.executemany(
            "INSERT INTO docs(base,dt2_off,pages,agency,kind,date,number,title)"
            " VALUES(?,?,?,?,?,?,?,?)", pend_docs)
        maxid = con.execute("SELECT MAX(id) FROM docs").fetchone()[0]
        first = maxid - len(pend_docs) + 1
        con.executemany("INSERT INTO fts_docs(rowid, tokens) VALUES(?,?)",
                        [(first + i, tok) for i, tok in enumerate(pend_tokens)])
        con.commit()
        pend_docs.clear()
        pend_tokens.clear()

    # Однопоточный конвейер: multiprocessing-Pool убит IPC-сериализацией
    # (замерено: один воркер 1034 стр/с против 660 стр/с у пула из 8).
    # Параллелизм — по базам (несколько процессов на разных базах).
    _init_worker(dt2)
    for offsets in batched(scan_offsets(dt2, limit), batch):
        results = process_batch(offsets)
        for off, is_start, agency, kind, date, number, title, tokens in results:
            n_pages += 1
            if is_start:
                close_doc()
            if not cur_pages:  # первая страница документа — её реквизиты
                cur_req = (agency, kind, date, number, title)
            cur_pages.append(off)
            cur_tokens.append(tokens)
        if len(pend_docs) >= 2000:
            flush()
            print(f"  {n_pages} pages -> {n_docs} docs, {time.time()-t0:.0f}s",
                  flush=True)
    close_doc()
    flush()
    print(f"pages: {n_pages} -> documents: {n_docs} in {time.time()-t0:.0f}s",
          flush=True)

    # заголовки DT1
    t1 = time.time()
    n = 0
    batch_h = []
    for off, raw in base.iter_headers(limit):
        fields = parse_header(raw)["raw_fields"]
        info = _header_info(fields)
        batch_h.append((base.name, off, info["kind"], info["number"],
                        info["date"], info["title"], info["source"], info["note"]))
        n += 1
        if len(batch_h) >= 10000:
            con.executemany(
                "INSERT INTO headers(base,dt1_off,kind,number,date,title,source,note)"
                " VALUES(?,?,?,?,?,?,?,?)", batch_h)
            con.commit()
            batch_h.clear()
    if batch_h:
        con.executemany(
            "INSERT INTO headers(base,dt1_off,kind,number,date,title,source,note)"
            " VALUES(?,?,?,?,?,?,?,?)", batch_h)
    con.commit()
    print(f"headers indexed: {n} in {time.time()-t1:.0f}s", flush=True)
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()
    index_base_fast(args.base_dir, args.db, args.limit, args.workers, args.batch)


if __name__ == "__main__":
    main()
