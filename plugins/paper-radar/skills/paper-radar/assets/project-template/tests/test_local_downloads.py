import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from paper_radar.local_downloads import ingest_recent_downloads
from paper_radar.models import Paper, ScoreBreakdown, ScoredPaper


class LocalDownloadTests(unittest.TestCase):
    def test_recent_generic_filename_pdf_matches_and_prioritizes_candidate(self):
        paper = Paper(
            "crossref",
            "10.1126/science.aeb6744",
            "Leaping out of the water: Aerial-aquatic locomotion with flapping wings",
            "flapping-wing robot locomotion",
            [],
            date.today(),
            venue="Science",
            doi="10.1126/science.aeb6744",
            raw={"paper_radar_recovery": {"tier": "tier1"}},
        )
        scored = ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1))
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "EBSCO-FullText-07_15_2026.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * 20_000)
            audit = Path(tmp) / "audit.json"
            with (
                patch(
                    "paper_radar.local_downloads._pdf_identity_text",
                    return_value=(paper.title + " 10.1126/science.aeb6744 " + "research " * 100),
                ),
                patch("paper_radar.local_downloads.validate_pdf_identity", return_value=(True, "ok")),
                patch("paper_radar.local_downloads.import_discovered_download", return_value=Path(tmp) / "recovered.pdf"),
            ):
                records = ingest_recent_downloads([scored], downloads_dir=tmp, audit_path=audit)

        self.assertEqual(len(records), 1)
        self.assertTrue(paper.raw["paper_radar_local_download"]["priority"])
        self.assertEqual(records[0]["source_file"], pdf.name)


if __name__ == "__main__":
    unittest.main()
