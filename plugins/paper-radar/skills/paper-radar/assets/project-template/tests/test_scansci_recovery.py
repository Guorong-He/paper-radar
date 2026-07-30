import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from paper_radar.models import Paper
from paper_radar.scansci_recovery import (
    _awaiting_current_myloft_attempt,
    _parse_runner_result,
    _valid_recovered_pdf,
    canonical_doi,
    recover_pdf_bytes,
)


class ScanSciRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_audit = os.environ.get("PAPER_RADAR_SCANSCI_AUDIT_PATH")
        os.environ["PAPER_RADAR_SCANSCI_AUDIT_PATH"] = str(Path(self.tmp.name) / "audit.jsonl")

    def tearDown(self):
        if self.old_audit is None:
            os.environ.pop("PAPER_RADAR_SCANSCI_AUDIT_PATH", None)
        else:
            os.environ["PAPER_RADAR_SCANSCI_AUDIT_PATH"] = self.old_audit
        self.tmp.cleanup()

    def test_recovered_pdf_rejects_supplementary_information(self):
        from unittest.mock import MagicMock, patch

        fake_reader = MagicMock()
        fake_reader.pages = [
            MagicMock(extract_text=lambda: "Target paper title Supplementary information " + "methods " * 120),
            MagicMock(extract_text=lambda: "additional experiments " * 80),
        ]
        with patch("paper_radar.scansci_recovery.PdfReader", return_value=fake_reader):
            self.assertFalse(
                _valid_recovered_pdf(b"%PDF" + b"x" * 10_000, "Target paper title")
            )

    def test_canonical_doi_normalizes_url_prefixes(self):
        paper = Paper(
            source="openalex",
            source_id="W123",
            title="Title",
            abstract="",
            authors=[],
            published_at=date.today(),
            doi="https://dx.doi.org/10.1126/SCIROBOTICS.ABC123V1",
        )

        self.assertEqual(canonical_doi(paper), "10.1126/scirobotics.abc123v1")

    def test_canonical_doi_uses_crossref_source_id(self):
        paper = Paper(
            source="crossref",
            source_id="10.1038/s41467-026-12345-6",
            title="Title",
            abstract="",
            authors=[],
            published_at=date.today(),
        )

        self.assertEqual(canonical_doi(paper), "10.1038/s41467-026-12345-6")

    def test_runner_result_uses_last_structured_line(self):
        stdout = "noise\nSCANSCI_RESULT=" + json.dumps({"success": True, "source": "PMC"})

        self.assertEqual(_parse_runner_result(stdout)["source"], "PMC")

    def test_runner_result_rejects_invalid_json(self):
        self.assertEqual(_parse_runner_result("SCANSCI_RESULT={broken"), {})

    def test_formal_paper_without_doi_is_recorded_for_audit(self):
        paper = Paper(
            source="openalex",
            source_id="W-no-doi",
            title="Formal paper without DOI",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Nature Communications",
        )
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            old_path = os.environ.get("PAPER_RADAR_SCANSCI_AUDIT_PATH")
            os.environ["PAPER_RADAR_SCANSCI_AUDIT_PATH"] = str(audit)
            try:
                self.assertIsNone(recover_pdf_bytes(paper, output_dir=Path(tmp) / "pdfs"))
            finally:
                if old_path is None:
                    os.environ.pop("PAPER_RADAR_SCANSCI_AUDIT_PATH", None)
                else:
                    os.environ["PAPER_RADAR_SCANSCI_AUDIT_PATH"] = old_path

            record = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "skipped_no_doi")

    def test_formal_recovery_waits_for_current_myloft_attempt(self):
        from unittest.mock import patch

        paper = Paper(
            source="openalex",
            source_id="W123",
            title="Embodied tactile robotics paper",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Science Robotics",
            doi="10.1126/scirobotics.example",
            raw={"paper_radar_recovery": {"tier": "tier1"}},
        )
        with (
            patch("paper_radar.scansci_recovery.has_terminal_recovery_failure", return_value=False),
            patch("paper_radar.scansci_recovery.enqueue_candidate", return_value=True) as enqueue,
            patch("paper_radar.scansci_recovery._record_audit"),
        ):
            self.assertTrue(_awaiting_current_myloft_attempt(paper, paper.doi))
        enqueue.assert_called_once()
        self.assertIn("direct MyLOFT publisher attempt", enqueue.call_args.args[2])

    def test_terminal_myloft_failure_releases_public_manuscript_recovery(self):
        from unittest.mock import patch

        paper = Paper(
            source="openalex",
            source_id="W123",
            title="Embodied tactile robotics paper",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Science Robotics",
            doi="10.1126/scirobotics.example",
            raw={"paper_radar_recovery": {"tier": "tier1"}},
        )
        with patch("paper_radar.scansci_recovery.has_terminal_recovery_failure", return_value=True):
            self.assertFalse(_awaiting_current_myloft_attempt(paper, paper.doi))

    def test_exhausted_recovery_does_not_repeat_public_lookup(self):
        from unittest.mock import patch

        paper = Paper(
            source="crossref",
            source_id="10.1126/scirobotics.example",
            title="Selected robotics paper",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Science Robotics",
            doi="10.1126/scirobotics.example",
            raw={"paper_radar_recovery": {"tier": "tier1"}},
        )
        with (
            patch("paper_radar.scansci_recovery.has_exhausted_recovery_paths", return_value=True),
            patch("paper_radar.scansci_recovery.subprocess.run") as runner,
        ):
            self.assertIsNone(recover_pdf_bytes(paper))

        runner.assert_not_called()

    def test_public_recovery_timeout_is_persistently_counted(self):
        from subprocess import TimeoutExpired
        from unittest.mock import patch

        paper = Paper(
            source="crossref",
            source_id="10.1126/sciadv.timeout",
            title="Embodied robot timeout paper",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Science Advances",
            doi="10.1126/sciadv.timeout",
            raw={"paper_radar_recovery": {"tier": "tier2"}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("paper_radar.scansci_recovery.has_exhausted_recovery_paths", return_value=False),
                patch("paper_radar.scansci_recovery._awaiting_current_myloft_attempt", return_value=False),
                patch("paper_radar.scansci_recovery.DEFAULT_SCANSCI_PYTHON", Path("/bin/sh")),
                patch("paper_radar.scansci_recovery.subprocess.run", side_effect=TimeoutExpired("runner", 1)),
                patch("paper_radar.scansci_recovery.record_public_recovery_timeout") as record_timeout,
                patch("paper_radar.scansci_recovery._record_audit"),
                patch("paper_radar.scansci_recovery._enqueue_if_not_terminal"),
            ):
                self.assertIsNone(recover_pdf_bytes(paper, output_dir=tmp))

        record_timeout.assert_called_once_with(paper.doi)


if __name__ == "__main__":
    unittest.main()
