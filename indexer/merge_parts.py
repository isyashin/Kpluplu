"""Merge document metadata from index_part1..3.db into index_new.db.

FTS5 contentless tables stay in the part files: their tokens cannot be read back
with SELECT and therefore cannot be copied into the merged database.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path


def main(root: Path) -> Path:
    root = root.resolve()
    out = root / "index_new.db"
    parts = [root / f"index_part{i}.db" for i in (1, 2, 3)]
    if out.exists():
        raise FileExistsError(f"Unfinished merged index already exists: {out}")
    for part in parts:
        if not part.is_file():
            raise FileNotFoundError(part)

    con = sqlite3.connect(out, timeout=120)
    try:
        con.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = OFF;
            CREATE TABLE docs(
                id INTEGER PRIMARY KEY, base TEXT NOT NULL, dt2_off INTEGER NOT NULL,
                pages TEXT NOT NULL DEFAULT '[]', agency TEXT, kind TEXT,
                date TEXT, number TEXT, title TEXT, part INTEGER NOT NULL,
                local_id INTEGER NOT NULL);
            CREATE TABLE headers(
                id INTEGER PRIMARY KEY, base TEXT NOT NULL, dt1_off INTEGER NOT NULL,
                kind TEXT, number TEXT, date TEXT, title TEXT, source TEXT, note TEXT);
            CREATE TABLE part_offsets(part INTEGER PRIMARY KEY, offset INTEGER NOT NULL);
        """)

        offset = 0
        for part_no, part_path in enumerate(parts, 1):
            started = time.monotonic()
            con.execute("ATTACH DATABASE ? AS p", (str(part_path),))
            try:
                con.execute("BEGIN")
                con.execute("""INSERT INTO docs
                    (id, base, dt2_off, pages, agency, kind, date, number, title, part, local_id)
                    SELECT id + ?, base, dt2_off, pages, agency, kind, date, number, title,
                           ?, id FROM p.docs ORDER BY id""", (offset, part_no))
                con.execute("""INSERT INTO headers
                    (base, dt1_off, kind, number, date, title, source, note)
                    SELECT base, dt1_off, kind, number, date, title, source, note
                    FROM p.headers""")
                con.execute("INSERT INTO part_offsets VALUES (?, ?)", (part_no, offset))
                con.commit()
                offset = con.execute("SELECT COALESCE(MAX(id), 0) FROM docs").fetchone()[0]
                print(f"part{part_no} merged in {time.monotonic() - started:.0f}s; "
                      f"max id {offset}", flush=True)
            except Exception:
                con.rollback()
                raise
            finally:
                con.execute("DETACH DATABASE p")

        con.executescript("""
            CREATE INDEX ix_docs_base ON docs(base);
            CREATE INDEX ix_docs_req ON docs(base, number, date);
            CREATE INDEX ix_docs_number ON docs(number);
            CREATE INDEX ix_headers_req ON headers(base, number, date);
            PRAGMA optimize;
        """)
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode = DELETE")
    finally:
        con.close()
    print("MERGE DONE", flush=True)
    return out


if __name__ == "__main__":
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        os.environ.get("CONSPLUS_HOME", Path(__file__).resolve().parent.parent))
    main(project)
