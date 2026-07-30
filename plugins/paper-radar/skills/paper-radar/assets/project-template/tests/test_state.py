import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from paper_radar.db import init_db, upsert_papers
from paper_radar.models import Paper, ScoreBreakdown, ScoredPaper
from paper_radar.pipeline import (
    apply_frozen_issue_slots,
    discover_candidates,
    warm_candidate_cache_from_database,
    write_issue_working_set,
)
from paper_radar.state import (
    compact_run_report,
    load_candidate_cache,
    mark_stage,
    record_run_event,
    save_candidate_cache,
)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = os.environ.copy()
        os.environ["PAPER_RADAR_STATE_ROOT"] = self.tmp.name
        os.environ["PAPER_RADAR_WORKING_SET_PATH"] = str(Path(self.tmp.name) / "state.json")
        os.environ["PAPER_RADAR_CANDIDATE_CACHE_PATH"] = str(Path(self.tmp.name) / "candidates.json.gz")
        os.environ["PAPER_RADAR_RUN_LEDGER_PATH"] = str(Path(self.tmp.name) / "run-events.jsonl")
        os.environ["PAPER_RADAR_RUN_DATE"] = "2026-07-19"
        os.environ["PAPER_RADAR_SOURCE_STATUS_PATH"] = str(Path(self.tmp.name) / "source.json")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def _scored(self, doi: str, title: str = "Frozen paper") -> ScoredPaper:
        paper = Paper(
            "crossref",
            doi,
            title,
            "abstract",
            [],
            date(2026, 7, 1),
            venue="Nature Communications",
            doi=doi,
            fulltext="x" * 1200,
            key_figure_path=str(Path(self.tmp.name) / f"{doi.rsplit('/', 1)[-1]}.png"),
            raw={"paper_radar_recovery": {"tier": "tier2"}},
        )
        Path(paper.key_figure_path).write_bytes(b"figure")
        return ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1))

    def test_frozen_slot_restores_from_snapshot_when_source_does_not_rediscover_it(self):
        profile = {
            "selection": {"target_count": 1},
            "tier1_broad_venues": [],
            "preferred_venues": ["Nature Communications"],
        }
        frozen = self._scored("10.1/frozen")
        write_issue_working_set([frozen], [frozen], profile)
        replacement = self._scored("10.1/replacement", "Replacement paper")

        result = apply_frozen_issue_slots([replacement], [replacement], profile)

        self.assertEqual(result[0].paper.doi, "10.1/frozen")
        self.assertGreaterEqual(len(result[0].paper.fulltext), 1000)

    def test_candidate_cache_round_trip_and_same_issue_cache_hit(self):
        today = date(2026, 7, 19)
        paper = self._scored("10.1/cache").paper
        paper.raw["large_source_payload"] = {"unused": "x" * 1000}
        paper.raw["type"] = "journal-article"
        save_candidate_cache(today, [paper], {"crossref": {"enabled": True, "ok": True}})

        loaded = load_candidate_cache(today)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0][0].doi, paper.doi)
        self.assertNotIn("large_source_payload", loaded[0][0].raw)
        self.assertEqual(loaded[0][0].raw["type"], "journal-article")

        with patch("paper_radar.pipeline.fetch_live_candidates", side_effect=AssertionError("network should not run")):
            papers, status = discover_candidates({"selection": {"lookback_days": 1095}}, today)

        self.assertEqual([item.doi for item in papers], [paper.doi])
        self.assertTrue(status["candidate_cache"]["cache_hit"])

    def test_empty_same_issue_cache_does_not_suppress_source_retry(self):
        today = date(2026, 7, 19)
        paper = self._scored("10.1/retry").paper
        save_candidate_cache(today, [], {"crossref": {"enabled": True, "ok": False}})

        with patch(
            "paper_radar.pipeline.fetch_live_candidates",
            return_value=([paper], {"crossref": {"enabled": True, "ok": True}}),
        ) as fetch:
            papers, status = discover_candidates(
                {"selection": {"lookback_days": 1095}},
                today,
                db_path=str(Path(self.tmp.name) / "missing.db"),
            )

        fetch.assert_called_once()
        self.assertEqual([item.doi for item in papers], [paper.doi])
        self.assertFalse(status["candidate_cache"]["cache_hit"])

    def test_new_issue_uses_prior_catalog_and_only_incremental_source_window(self):
        prior_date = date(2026, 7, 12)
        today = date(2026, 7, 19)
        base = self._scored("10.1/base").paper
        recent = self._scored("10.1/recent").paper
        old_override = os.environ.pop("PAPER_RADAR_CANDIDATE_CACHE_PATH", None)
        try:
            save_candidate_cache(prior_date, [base], {"database_bootstrap": {"ok": True}})
            with patch(
                "paper_radar.pipeline.fetch_live_candidates",
                return_value=([recent], {"crossref": {"enabled": True, "ok": True}}),
            ) as fetch:
                papers, status = discover_candidates(
                    {
                        "selection": {
                            "lookback_days": 1095,
                            "candidate_incremental_days": 21,
                        }
                    },
                    today,
                    db_path=str(Path(self.tmp.name) / "missing.db"),
                )
        finally:
            if old_override is not None:
                os.environ["PAPER_RADAR_CANDIDATE_CACHE_PATH"] = old_override

        incremental_profile = fetch.call_args.args[0]
        self.assertEqual(incremental_profile["selection"]["backfill_days"], 21)
        self.assertEqual(incremental_profile["selection"]["official_backfill_days"], 21)
        self.assertEqual({paper.doi for paper in papers}, {base.doi, recent.doi})
        self.assertTrue(status["candidate_cache"]["incremental_refresh"])

    def test_working_set_preserves_stage_checkpoint_and_report_counts(self):
        today = date(2026, 7, 19)
        paper = self._scored("10.1/report")
        profile = {"selection": {"target_count": 1}}
        mark_stage(today, "discovery", "complete", {"candidate_count": 20})
        record_run_event(today, "test-run", "discovery", "complete", {"candidate_count": 20})

        write_issue_working_set([paper], [paper], profile)
        report = compact_run_report(today)

        self.assertEqual(report["completed_count"], 1)
        self.assertEqual(report["incomplete_count"], 0)
        self.assertEqual(report["stages"]["discovery"]["status"], "complete")
        self.assertEqual(report["recent_events"][-1]["run_id"], "test-run")

    def test_database_warm_cache_excludes_future_metadata(self):
        issue_date = date(2026, 7, 12)
        db_path = str(Path(self.tmp.name) / "paper_radar.db")
        init_db(db_path)
        valid = self._scored("10.1/valid").paper
        valid.published_at = date(2026, 7, 1)
        future = self._scored("10.1/future").paper
        future.published_at = date(2121, 1, 1)
        upsert_papers(db_path, [valid, future])
        profile = {
            "selection": {
                "lookback_days": 1095,
                "future_publication_grace_days": 7,
            }
        }

        report = warm_candidate_cache_from_database(
            db_path,
            profile,
            issue_date,
        )
        loaded = load_candidate_cache(issue_date)

        self.assertEqual(report["status"], "warmed")
        self.assertEqual([paper.doi for paper in loaded[0]], [valid.doi])


if __name__ == "__main__":
    unittest.main()
