"""Small regression fixtures for search order and relation extraction."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from api import main as api
from indexer import build_relations, merge_parts, parallel


class SearchOrderTest(unittest.TestCase):
    def test_filtered_pages_have_one_stable_order(self):
        with tempfile.TemporaryDirectory() as directory:
            main_db = Path(directory) / "index.db"
            part_db = Path(directory) / "index_part1.db"
            with closing(sqlite3.connect(main_db)) as con, con:
                con.execute("""CREATE TABLE docs (
                    id INTEGER PRIMARY KEY, base TEXT, kind TEXT, number TEXT,
                    date TEXT, title TEXT, agency TEXT, dt2_off INTEGER, pages TEXT
                )""")
                con.executemany(
                    "INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?)",
                    [(i, "LAW", "ЗАКОН" if i % 2 else "ПИСЬМО", str(i),
                      f"2020-01-{i:02d}", f"Документ {i}", "", i, "[]")
                     for i in range(1, 13)],
                )
            with closing(sqlite3.connect(part_db)) as con, con:
                con.execute("CREATE TABLE docs (id INTEGER PRIMARY KEY, base TEXT, kind TEXT, date TEXT)")
                con.execute("CREATE VIRTUAL TABLE fts_docs USING fts5(tokens, content='')")
                for i in range(1, 13):
                    con.execute("INSERT INTO docs VALUES (?,?,?,?)",
                                (i, "LAW", "ЗАКОН" if i % 2 else "ПИСЬМО", f"2020-01-{i:02d}"))
                    con.execute("INSERT INTO fts_docs(rowid, tokens) VALUES (?,?)", (i, "закупк"))

            def ids(per_page, page=1, **filters):
                return [item["id"] for item in api.search(
                    q="закупки", base="LAW", kind=filters.get("kind"),
                    date_from=filters.get("date_from"), date_to=None,
                    page=page, per_page=per_page)["items"]]

            with (patch.object(api, "INDEX_DB", main_db),
                  patch.object(api, "PARTS", [(1, part_db, 0)]),
                  patch.object(api, "_base_part", {"LAW": 1}),
                  patch.object(api, "_base_count", {"LAW": 1000}),
                  patch.object(api, "fetch_text", return_value="Закупки")):
                for filters in ({}, {"kind": "ЗАКОН"}, {"date_from": "2020-01-06"}):
                    ten = ids(10, **filters)
                    self.assertEqual(ids(3, **filters), ten[:3])
                    self.assertEqual(ids(3, page=2, **filters), ten[3:6])

            with (patch.object(api, "INDEX_DB", main_db),
                  patch.object(api, "_base_part", {"LAW": 1}),
                  patch.object(api, "_base_count", {"LAW": 12}),
                  patch.object(api, "fetch_text", return_value="Закупки")):
                self.assertTrue(all(i % 2 for i in ids(10, kind="ЗАКОН")))
                self.assertTrue(all(i >= 6 for i in ids(10, date_from="2020-01-06")))


class RequisitesTest(unittest.TestCase):
    def test_text_opening_separates_act_from_overview_and_citation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.db"
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("""CREATE TABLE docs (
                    id INTEGER PRIMARY KEY, base TEXT, kind TEXT, number TEXT,
                    date TEXT, title TEXT, agency TEXT, dt2_off INTEGER
                )""")
                con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?)", [
                    (1, "LAW", "", "44-ФЗ", "2013-04-05", "", "", 1),
                    (2, "LAW", "", "44-ФЗ", "2013-04-05", "", "", 2),
                    (3, "LAW", "", "44-ФЗ", "2013-04-05", "", "", 3),
                ])
            texts = {
                1: "ОБЗОР ИЗМЕНЕНИЙ ФЕДЕРАЛЬНОГО ЗАКОНА от 05.04.2013 N 44-ФЗ",
                2: "Федеральный закон от 05.04.2013 N 44-ФЗ О контрактной системе",
                3: ("ФЕДЕРАЛЬНАЯ АНТИМОНОПОЛЬНАЯ СЛУЖБА. РАЗЪЯСНЕНИЯ ПО ВОПРОСУ "
                    "ПРОВЕДЕНИЯ СОВМЕСТНЫХ ЗАКУПОК. Согласно статье 44-ФЗ от 05.04.2013"),
            }
            with (patch.object(api, "INDEX_DB", path),
                  patch.object(api, "fetch_text", side_effect=lambda base, off, pages: texts[off])):
                items = api.by_requisites("44-ФЗ", "2013-04-05", "LAW")["items"]
            self.assertEqual([item["id"] for item in items], [2, 1, 3])
            self.assertEqual([item["header_match"] for item in items], [True, False, False])


class RelationExtractionTest(unittest.TestCase):
    def test_empty_and_duplicate_edges_are_not_built(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.db"
            note = ("Документ фактически утратил силу в связи с принятием "
                    "Федерального закона от 05.04.2013 N 44-ФЗ.")
            with closing(sqlite3.connect(path)) as con, con:
                con.execute("""CREATE TABLE headers (
                    base TEXT, kind TEXT, number TEXT, date TEXT, title TEXT, note TEXT
                )""")
                con.execute("CREATE TABLE relations (old_marker TEXT)")
                con.executemany("INSERT INTO headers VALUES (?,?,?,?,?,?)", [
                    ("LAW", "ПИСЬМО", "п-1", "2012-01-01", "Письмо", note),
                    ("LAW", "ПИСЬМО", "п-1", "2012-01-01", "Письмо", note),
                    ("LAW", "", "", None, "", note),
                    ("LAW", "ПИСЬМО", "п-2", "2012-01-02", "Письмо", "Документ утратил силу."),
                ])
            build_relations.main(str(path))
            with closing(sqlite3.connect(path)) as con, con:
                rows = con.execute("SELECT src_number, relation, target_number, target_date FROM relations").fetchall()
                self.assertEqual(rows, [("п-1", "repealed_by", "44-фз", "2013-04-05")])
                self.assertIsNone(con.execute(
                    "SELECT name FROM sqlite_master WHERE name='relations_next'").fetchone())


class MergeTest(unittest.TestCase):
    def test_merge_preserves_local_ids_and_keeps_fts_in_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for part in (1, 2, 3):
                path = root / f"index_part{part}.db"
                with closing(sqlite3.connect(path)) as con, con:
                    con.execute("""CREATE TABLE docs (
                        id INTEGER PRIMARY KEY, base TEXT, dt2_off INTEGER, pages TEXT,
                        agency TEXT, kind TEXT, date TEXT, number TEXT, title TEXT
                    )""")
                    con.execute("""CREATE TABLE headers (
                        id INTEGER PRIMARY KEY, base TEXT, dt1_off INTEGER, kind TEXT,
                        number TEXT, date TEXT, title TEXT, source TEXT, note TEXT
                    )""")
                    con.execute("CREATE VIRTUAL TABLE fts_docs USING fts5(tokens, content='')")
                    con.execute("INSERT INTO docs VALUES (1, ?, 0, '[]', '', '', '', '', '')",
                                (f"BASE{part}",))
                    con.execute("INSERT INTO headers VALUES (1, ?, 0, '', '', '', '', '', '')",
                                (f"BASE{part}",))
                    con.execute("INSERT INTO fts_docs(rowid, tokens) VALUES (1, 'закупк')")

            merged = merge_parts.main(root)
            with closing(sqlite3.connect(merged)) as con:
                self.assertEqual(con.execute(
                    "SELECT id, part, local_id FROM docs ORDER BY id").fetchall(),
                    [(1, 1, 1), (2, 2, 1), (3, 3, 1)])
                self.assertEqual(con.execute(
                    "SELECT part, offset FROM part_offsets ORDER BY part").fetchall(),
                    [(1, 0), (2, 1), (3, 2)])
                self.assertIsNone(con.execute(
                    "SELECT name FROM sqlite_master WHERE name='fts_docs'").fetchone())

            with patch.object(api, "INDEX_DB", merged), patch.object(
                    api, "PARTS", [(i, root / f"index_part{i}.db", 0) for i in (1, 2, 3)]):
                self.assertEqual([x[2] for x in api.search_parts()], [0, 1, 2])


class ParallelIndexTest(unittest.TestCase):
    def test_failed_base_does_not_mark_group_done(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "groups.json").write_text('[["LAW"], [], []]', encoding="utf-8")
            with (patch.object(parallel, "ROOT", root),
                  patch.object(parallel, "BASE", root / "BASE"),
                  patch.object(parallel.subprocess, "run", return_value=Mock(returncode=7))):
                with self.assertRaisesRegex(RuntimeError, "LAW"):
                    parallel.run_group(1)
            log = (root / "index_part1.log").read_text(encoding="utf-8")
            self.assertIn("FAILED: LAW", log)
            self.assertNotIn("done in", log)


if __name__ == "__main__":
    unittest.main()
