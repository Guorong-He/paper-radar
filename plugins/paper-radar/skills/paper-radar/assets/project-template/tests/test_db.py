import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from paper_radar.db import init_db, load_candidate_papers, upsert_papers
from paper_radar.models import Paper


class DatabaseTests(unittest.TestCase):
    def test_upsert_papers_replaces_invalid_unicode_surrogates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_radar.db")
            init_db(db_path)
            paper = Paper(
                source="crossref",
                source_id="10.test/surrogate",
                title="Bad \ud835 title",
                abstract="robot sensing",
                authors=["A \ud835 Author"],
                published_at=date.today(),
                venue="Science Robotics",
                raw={"bad": "value \ud835"},
            )

            upsert_papers(db_path, [paper])

            import sqlite3

            with sqlite3.connect(db_path) as conn:
                row = conn.execute("select title, authors_json, raw_json from papers").fetchone()
            self.assertIn("?", row[0])
            self.assertIn("?", json.loads(row[1])[0])
            self.assertEqual(json.loads(row[2])["bad"], "value \ud835")

    def test_candidate_catalog_loader_enforces_both_date_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "paper_radar.db")
            init_db(db_path)
            papers = [
                Paper("crossref", "valid", "Valid", "", [], date(2026, 7, 10)),
                Paper("crossref", "future", "Future", "", [], date(2121, 1, 1)),
                Paper("crossref", "old", "Old", "", [], date(2020, 1, 1)),
            ]
            upsert_papers(db_path, papers)

            loaded = load_candidate_papers(
                db_path,
                date(2023, 7, 12),
                date(2026, 7, 19),
            )

        self.assertEqual([paper.source_id for paper in loaded], ["valid"])


if __name__ == "__main__":
    unittest.main()
