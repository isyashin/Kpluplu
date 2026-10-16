"""Построение графа связей из аннотаций заголовков.

Паттерны аннотаций КонсультантПлюс:
  "Документ утратил силу ... в связи с изданием Приказа ФТС РФ от 31.03.2010 N 663."
  "В документ внесены изменения Постановлением ... от ... N ..."
  "Документ вступил в силу с ..."
Результат: relations(base, src_number, src_date, relation, target_number, target_date, target_raw)
"""
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from consplus.requisites import normalize_number, MONTHS

# "в связи с изданием Приказа ФТС РФ от 31.03.2010 N 663"
RE_TARGET = re.compile(
    r"(?:изданием|принятием|утверждением|издани[ея]ю?м?)\s+"
    r"(?P<raw>(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]+)\b[^.]*?)"
    r"\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})\s*N\s*(?P<num>[^.,\s]+)",
    re.IGNORECASE)

REL_RULES = [
    (r"утратил силу|признан утратившим|не подлежит применению", "repealed_by"),
    (r"утрачивает силу", "to_be_repealed_by"),
    (r"внесены изменения|изменен\b", "amended_by"),
    (r"новая редакция|в новой редакции", "new_edition_by"),
    (r"вступил в силу|вступает в силу", "in_force"),
]


def parse_relations(note: str) -> list[tuple[str, str, str, str]]:
    """Из текста аннотации -> [(relation, target_number, target_date, raw)]."""
    low = note.lower()
    out = []
    for pat, rel in REL_RULES:
        if re.search(pat, low):
            m = RE_TARGET.search(note)
            if m:
                d = m.group("date").split(".")
                out.append((rel, normalize_number(m.group("num")),
                            f"{d[2]}-{d[1]}-{d[0]}", m.group("raw")[:120]))
            break
    return out


def main(db_path: str):
    con = sqlite3.connect(db_path)
    # До завершения разбора сохраняем прежнюю таблицу связей.
    con.execute("DROP TABLE IF EXISTS relations_next")
    con.execute("""CREATE TABLE relations_next(
        id INTEGER PRIMARY KEY,
        base TEXT, src_number TEXT, src_date TEXT, src_title TEXT,
        relation TEXT, target_number TEXT, target_date TEXT, target_raw TEXT)""")

    stats = Counter()
    batch = []
    seen = set()
    for base, kind, number, date, title, note in con.execute(
            "SELECT base, kind, number, date, title, note FROM headers WHERE note != ''"):
        if not number:
            continue
        for rel, tnum, tdate, raw in parse_relations(note):
            if not tnum:
                continue
            key = (base, number, date, rel, tnum, tdate)
            if key in seen:
                continue
            seen.add(key)
            batch.append((base, number, date, title, rel, tnum, tdate, raw))
            stats[rel] += 1
        if len(batch) >= 50000:
            con.executemany("INSERT INTO relations_next(base,src_number,src_date,src_title,"
                            "relation,target_number,target_date,target_raw)"
                            " VALUES(?,?,?,?,?,?,?,?)", batch)
            con.commit()
            batch.clear()
    if batch:
        con.executemany("INSERT INTO relations_next(base,src_number,src_date,src_title,"
                        "relation,target_number,target_date,target_raw)"
                        " VALUES(?,?,?,?,?,?,?,?)", batch)
    con.commit()
    con.execute("BEGIN")
    con.execute("DROP TABLE IF EXISTS relations")
    con.execute("ALTER TABLE relations_next RENAME TO relations")
    con.execute("CREATE INDEX ix_rel_src ON relations(base, src_number, src_date)")
    con.execute("CREATE INDEX ix_rel_tgt ON relations(base, target_number, target_date)")
    print(dict(stats))
    print("total:", con.execute("SELECT COUNT(*) FROM relations").fetchone()[0])
    con.commit()
    con.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "index.db"))
