import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

import paper_radar.myloft as myloft
from paper_radar.models import Paper
from pypdf import PdfWriter


class MyLoftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ["PAPER_RADAR_RUN_DATE"] = "2026-07-02"
        os.environ["PAPER_RADAR_MYLOFT_QUEUE_PATH"] = str(root / "queue.json")
        os.environ["PAPER_RADAR_MYLOFT_LEDGER_PATH"] = str(root / "ledger.json")
        os.environ["PAPER_RADAR_MYLOFT_MAX_PER_ISSUE"] = "8"
        os.environ["PAPER_RADAR_MYLOFT_MAX_PER_24_HOURS"] = "10"
        os.environ["PAPER_RADAR_MYLOFT_MIN_INTERVAL_SECONDS"] = "60"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def _paper(self, index: int) -> Paper:
        return Paper(
            source="crossref",
            source_id=f"10.1126/scirobotics.test{index}",
            title=f"Selected formal robotics paper {index}",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Science Robotics",
            doi=f"10.1126/scirobotics.test{index}",
        )

    def test_queue_keeps_all_tier_candidates_but_only_eight_are_download_eligible(self):
        results = [
            myloft.enqueue_candidate(self._paper(index), self._paper(index).doi, "public recovery miss")
            for index in range(10)
        ]

        self.assertTrue(all(results))
        status = myloft.queue_status()
        self.assertEqual(len(status["papers"]), 10)
        self.assertEqual(sum(item["download_eligible"] for item in status["papers"]), 8)
        self.assertEqual(status["mode"], "visible_browser_sequential_only")
        self.assertEqual(status["rate_limit"]["min_interval_seconds"], 60)

    def test_import_requires_a_current_pending_queue_item(self):
        with self.assertRaises(ValueError):
            myloft.import_download("10.1126/scirobotics.missing", Path(self.tmp.name) / "paper.pdf")

    def test_skip_releases_a_pending_queue_slot(self):
        papers = [self._paper(index) for index in range(10)]
        for paper in papers[:9]:
            self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        self.assertTrue(myloft.skip_candidate(papers[0].doi, "publisher entitlement unavailable"))
        self.assertTrue(myloft.enqueue_candidate(papers[9], papers[9].doi, "public recovery miss"))
        status = myloft.queue_status()
        self.assertEqual(sum(item["status"] == "pending" for item in status["papers"]), 9)
        self.assertEqual(sum(item.get("download_eligible", False) for item in status["papers"]), 8)

    def test_reconcile_retires_stale_pending_entries_without_deleting_history(self):
        stale = self._paper(1)
        current = self._paper(2)
        self.assertTrue(myloft.enqueue_candidate(stale, stale.doi, "public recovery miss"))
        self.assertTrue(myloft.enqueue_candidate(current, current.doi, "public recovery miss"))

        retired = myloft.reconcile_pending_candidates([current])
        status = myloft.queue_status()

        self.assertEqual(retired, 1)
        stale_item = next(item for item in status["papers"] if item["doi"] == stale.doi)
        current_item = next(item for item in status["papers"] if item["doi"] == current.doi)
        self.assertEqual(stale_item["status"], "skipped")
        self.assertEqual(
            stale_item["skip_reason"],
            "absent_from_current_strict_semantic_candidate_set",
        )
        self.assertEqual(current_item["status"], "pending")

    def test_current_strict_candidate_can_reactivate_only_a_stale_retirement(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        self.assertEqual(myloft.reconcile_pending_candidates([]), 1)

        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        item = myloft.queue_status()["papers"][0]

        self.assertEqual(item["status"], "pending")
        self.assertEqual(
            item["previous_skip_reason"],
            "absent_from_current_strict_semantic_candidate_set",
        )

    def test_direct_publisher_failure_cannot_be_reactivated(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        self.assertTrue(myloft.skip_candidate(paper.doi, "publisher entitlement unavailable"))

        self.assertFalse(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))

    def test_terminal_recovery_failure_excludes_only_direct_publisher_outcomes(self):
        stale = self._paper(1)
        terminal = self._paper(2)
        self.assertTrue(myloft.enqueue_candidate(stale, stale.doi, "public recovery miss"))
        self.assertTrue(myloft.enqueue_candidate(terminal, terminal.doi, "public recovery miss"))
        self.assertEqual(myloft.reconcile_pending_candidates([terminal]), 1)
        self.assertTrue(myloft.skip_candidate(terminal.doi, "publisher entitlement unavailable"))

        self.assertFalse(myloft.has_terminal_recovery_failure(stale.doi))
        self.assertTrue(myloft.has_terminal_recovery_failure(terminal.doi))

    def test_exhausted_recovery_requires_both_direct_and_public_failures(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        self.assertTrue(myloft.skip_candidate(paper.doi, "publisher entitlement unavailable"))

        self.assertFalse(myloft.has_exhausted_recovery_paths(paper.doi))
        self.assertTrue(myloft.mark_public_recovery_terminal(paper.doi, "no approved public PDF"))
        self.assertTrue(myloft.has_exhausted_recovery_paths(paper.doi))

    def test_two_public_recovery_timeouts_exhaust_only_that_paper(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        self.assertTrue(myloft.skip_candidate(paper.doi, "publisher entitlement unavailable"))

        self.assertFalse(myloft.record_public_recovery_timeout(paper.doi))
        self.assertFalse(myloft.has_exhausted_recovery_paths(paper.doi))
        self.assertTrue(myloft.record_public_recovery_timeout(paper.doi))
        self.assertTrue(myloft.has_exhausted_recovery_paths(paper.doi))

    def test_repeated_double_failure_marks_only_that_current_issue_venue_unavailable(self):
        papers = [self._paper(index) for index in range(3)]
        for paper in papers:
            self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
            self.assertTrue(myloft.skip_candidate(paper.doi, "publisher entitlement unavailable"))
            self.assertTrue(myloft.mark_public_recovery_terminal(paper.doi, "no approved public PDF"))

        self.assertEqual(myloft.exhausted_recovery_venues(), {"science robotics"})

    def test_imported_recovery_remains_available_after_same_venue_failures(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        source = Path(self.tmp.name) / "paper.pdf"
        source.write_bytes(b"%PDF-test payload")
        old_validator = myloft.validate_pdf_identity
        myloft.validate_pdf_identity = lambda *_args, **_kwargs: (True, "ok")
        try:
            myloft.import_download(paper.doi, source, recovery_dir=Path(self.tmp.name) / "recovered")
        finally:
            myloft.validate_pdf_identity = old_validator

        self.assertTrue(myloft.has_imported_recovery(paper.doi))

    def test_historical_skip_is_reactivated_for_current_strict_selection(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        queue_path = Path(os.environ["PAPER_RADAR_MYLOFT_QUEUE_PATH"])
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue["papers"][0].update(
            status="skipped",
            skip_reason="AAAS publisher entitlement unavailable for current authorized session",
        )
        queue_path.write_text(json.dumps(queue), encoding="utf-8")

        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "public recovery miss"))
        item = myloft.queue_status()["papers"][0]

        self.assertEqual(item["status"], "pending")
        self.assertIn("previous_skip_reason", item)

    def test_successful_import_writes_myloft_provenance_and_ledger(self):
        paper = self._paper(1)
        myloft.enqueue_candidate(paper, paper.doi, "public recovery miss")
        source = Path(self.tmp.name) / "paper.pdf"
        source.write_bytes(b"%PDF-test payload")
        old_validator = myloft.validate_pdf_identity
        myloft.validate_pdf_identity = lambda *_args: (True, "ok")
        try:
            target = myloft.import_download(paper.doi, source, recovery_dir=Path(self.tmp.name) / "recovered")
        finally:
            myloft.validate_pdf_identity = old_validator

        provenance = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["source"], "MyLOFT")
        self.assertEqual(provenance["institution"], "Tsinghua University")
        self.assertEqual(myloft.queue_status()["papers"][0]["status"], "imported")
        self.assertEqual(myloft.queue_status()["rate_limit"]["issue_import_count"], 1)

    def test_discovered_download_revives_a_prematurely_skipped_candidate(self):
        paper = self._paper(1)
        self.assertTrue(myloft.enqueue_candidate(paper, paper.doi, "await browser download"))
        self.assertTrue(myloft.skip_candidate(paper.doi, "login redirect before download completed"))
        self.assertTrue(myloft.mark_public_recovery_terminal(paper.doi, "not found"))
        source = Path(self.tmp.name) / "EBSCO-FullText.pdf"
        source.write_bytes(b"%PDF-test payload")
        old_validator = myloft.validate_pdf_identity
        myloft.validate_pdf_identity = lambda *_args, **_kwargs: (True, "ok")
        try:
            target = myloft.import_discovered_download(
                paper,
                source,
                recovery_dir=Path(self.tmp.name) / "recovered",
            )
        finally:
            myloft.validate_pdf_identity = old_validator

        item = myloft.queue_status()["papers"][0]
        provenance = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(item["status"], "imported")
        self.assertNotIn("public_recovery_attempted", item)
        self.assertEqual(provenance["source"], "LocalDownload")
        self.assertEqual(myloft.queue_status()["rate_limit"]["issue_import_count"], 0)

    def test_pdf_identity_accepts_strong_title_and_doi_article_identifier(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        writer.add_metadata(
            {
                "/Title": "Intermetallic-anchored epidermal EGaIn patch with analog constriction gates for cardiorespiratory monitoring",
                "/Subject": "Sci. Adv. 2026.12:eaee5907 " + ("research article " * 2000),
            }
        )
        source = Path(self.tmp.name) / "aaas.pdf"
        with source.open("wb") as handle:
            writer.write(handle)
        payload = source.read_bytes()

        ok, detail = myloft.validate_pdf_identity(
            payload,
            "Intermetallic-anchored epidermal EGaIn patch with analog constriction gates for cardiorespiratory monitoring",
            "10.1126/sciadv.aee5907",
        )

        self.assertTrue(ok, detail)

    def test_local_download_can_use_very_strong_title_when_database_pdf_omits_doi(self):
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        title = "Leaping out of the water: Aerial-aquatic locomotion with flapping wings"
        writer.add_metadata({"/Title": title, "/Subject": title + " " + ("research article " * 2000)})
        source = Path(self.tmp.name) / "EBSCO-FullText.pdf"
        with source.open("wb") as handle:
            writer.write(handle)

        ok, detail = myloft.validate_pdf_identity(
            source.read_bytes(),
            title,
            "10.1126/science.aeb6744",
            allow_strong_title_without_doi=True,
        )

        self.assertTrue(ok, detail)
        self.assertEqual(detail, "ok_strong_title_identity")


if __name__ == "__main__":
    unittest.main()
