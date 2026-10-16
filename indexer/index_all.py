"""Индексация всех баз каталога BASE в один index.db.

Использование: python indexer/index_all.py [BASE_DIR] [DB_PATH] [--only LAW,EXP]
"""
import argparse
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.environ.get("CONSPLUS_HOME", str(Path(__file__).parent.parent)))
from indexer.index_base import index_base

ROOT = Path(__file__).parent.parent
DEFAULT_BASE = os.environ.get("CONSPLUS_BASES", str(ROOT / "BASE"))
DEFAULT_DB = os.environ.get("CONSPLUS_INDEX", str(ROOT / "index.db"))


def list_bases(base_dir: Path) -> list[str]:
    out = []
    for d in sorted(base_dir.iterdir()):
        if d.is_dir() and (d / f"{d.name}.DT2").exists() and (d / f"{d.name}.DT1").exists():
            out.append(d.name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", default=DEFAULT_BASE)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--only", default=None, help="список баз через запятую")
    ap.add_argument("--skip-existing", action="store_true",
                    help="пропустить базы, уже есть в БД")
    ap.add_argument("--no-fts", action="store_true")
    args = ap.parse_args()

    base_dir = Path(args.base_dir)
    names = list_bases(base_dir)
    if args.only:
        want = set(args.only.split(","))
        names = [n for n in names if n in want]
    if args.skip_existing:
        import sqlite3
        con = sqlite3.connect(args.db)
        try:
            have = {r[0] for r in con.execute("SELECT DISTINCT base FROM docs")}
        except sqlite3.Error:
            have = set()
        con.close()
        names = [n for n in names if n not in have]
        print(f"skip existing: {len(have)}")
    print(f"bases to index: {len(names)}")

    failed = []
    t_all = time.time()
    for i, name in enumerate(names, 1):
        t0 = time.time()
        print(f"[{i}/{len(names)}] {name}...", flush=True)
        try:
            index_base(str(base_dir / name), args.db, with_fts=not args.no_fts)
        except Exception:
            failed.append(name)
            traceback.print_exc()
        print(f"    done in {time.time()-t0:.0f}s", flush=True)
    print(f"\nALL DONE in {time.time()-t_all:.0f}s. failed: {failed}")


if __name__ == "__main__":
    main()
