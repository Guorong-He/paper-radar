import os
import tempfile
import unittest
import json
from datetime import date, timedelta
from pathlib import Path

import paper_radar.pipeline as pipeline_module
from paper_radar.models import Paper, ScoredPaper, ScoreBreakdown
from paper_radar.pipeline import (
    _arxiv_circuit_failure_limit,
    all_configured_sources_unreachable,
    build_figure_candidate_pool,
    check_research_packet_history,
    current_run_date,
    dedupe_papers,
    exclude_terminal_recovery_failures,
    exclude_previously_recommended_papers,
    fetch_live_candidates,
    filter_candidate_publication_dates,
    has_complete_research_packet,
    apply_frozen_issue_slots,
    preserve_existing_complete_packet_when_incomplete,
    select_digest_requiring_key_figures,
    write_prepare_status,
)


class PipelineTests(unittest.TestCase):
    def test_publication_date_gate_rejects_stale_and_far_future_metadata(self):
        today = date(2026, 7, 19)
        profile = {
            "selection": {
                "lookback_days": 1095,
                "future_publication_grace_days": 7,
            }
        }
        papers = [
            Paper("crossref", "today", "Today", "", [], today),
            Paper("crossref", "grace", "Grace", "", [], today + timedelta(days=7)),
            Paper("crossref", "future", "Future", "", [], today + timedelta(days=8)),
            Paper("crossref", "bad", "Bad future", "", [], date(2121, 1, 1)),
            Paper("crossref", "old", "Old", "", [], today - timedelta(days=1096)),
        ]

        accepted, rejected = filter_candidate_publication_dates(papers, profile, today)

        self.assertEqual([paper.source_id for paper in accepted], ["today", "grace"])
        self.assertEqual(
            {item["paper_id"]: item["reason"] for item in rejected},
            {
                "crossref:future": "future_date",
                "crossref:bad": "future_date",
                "crossref:old": "before_lookback",
            },
        )

    def test_arxiv_circuit_failure_limit_defaults_and_can_be_configured(self):
        old_value = os.environ.get("PAPER_RADAR_ARXIV_CIRCUIT_FAILURES")
        try:
            os.environ.pop("PAPER_RADAR_ARXIV_CIRCUIT_FAILURES", None)
            self.assertEqual(_arxiv_circuit_failure_limit(), 3)
            os.environ["PAPER_RADAR_ARXIV_CIRCUIT_FAILURES"] = "5"
            self.assertEqual(_arxiv_circuit_failure_limit(), 5)
        finally:
            if old_value is None:
                os.environ.pop("PAPER_RADAR_ARXIV_CIRCUIT_FAILURES", None)
            else:
                os.environ["PAPER_RADAR_ARXIV_CIRCUIT_FAILURES"] = old_value

    def test_hydrate_fulltexts_recovers_formal_paper_without_pdf_url(self):
        paper = Paper(
            "crossref",
            "10.1126/sciadv.example",
            "Formal paper without a direct PDF URL",
            "abstract",
            [],
            date.today(),
            doi="10.1126/sciadv.example",
        )
        scored = ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1))
        old_fetch = pipeline_module.fetch_fulltext
        try:
            pipeline_module.fetch_fulltext = lambda _paper: "Recovered full text"
            pipeline_module.hydrate_fulltexts([scored])
        finally:
            pipeline_module.fetch_fulltext = old_fetch

        self.assertEqual(paper.fulltext, "Recovered full text")

    def test_figure_timeout_queues_selected_formal_paper_for_direct_recovery(self):
        paper = Paper(
            "crossref",
            "10.1038/s42256-025-00988-x",
            "Formal paper with a timed-out Figure 1 request",
            "",
            [],
            date.today(),
            doi="10.1038/s42256-025-00988-x",
            raw={"paper_radar_recovery": {"tier": "tier1"}},
        )
        scored = ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1))
        old_materialize = pipeline_module.materialize_key_figures
        old_enqueue = pipeline_module.enqueue_candidate
        old_timeout = os.environ.get("PAPER_RADAR_FIGURE_TIMEOUT_SECONDS")
        calls = []
        try:
            pipeline_module.materialize_key_figures = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError())
            pipeline_module.enqueue_candidate = lambda *args: calls.append(args) or True
            os.environ["PAPER_RADAR_FIGURE_TIMEOUT_SECONDS"] = "1"

            pipeline_module._materialize_key_figure_with_timeout(scored)
        finally:
            pipeline_module.materialize_key_figures = old_materialize
            pipeline_module.enqueue_candidate = old_enqueue
            if old_timeout is None:
                os.environ.pop("PAPER_RADAR_FIGURE_TIMEOUT_SECONDS", None)
            else:
                os.environ["PAPER_RADAR_FIGURE_TIMEOUT_SECONDS"] = old_timeout

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], paper.doi)
        self.assertIn("direct MyLOFT publisher attempt", calls[0][2])

    def test_terminal_formal_recovery_failure_is_removed_before_same_tier_reselection(self):
        formal_failed = ScoredPaper(
            Paper("crossref", "failed", "Failed formal", "", [], date.today(), doi="10.1/failed"),
            ScoreBreakdown(1, 1, 1, 1, 1, 1),
        )
        formal_available = ScoredPaper(
            Paper("crossref", "available", "Available formal", "", [], date.today(), doi="10.1/available"),
            ScoreBreakdown(1, 1, 1, 1, 1, 0.9),
        )
        preprint = ScoredPaper(
            Paper("arxiv", "2601.00001", "Preprint", "", [], date.today(), doi="10.1/failed"),
            ScoreBreakdown(1, 1, 1, 1, 1, 0.8),
        )
        old_terminal = pipeline_module.has_exhausted_recovery_paths
        try:
            pipeline_module.has_exhausted_recovery_paths = lambda doi: doi == "10.1/failed"
            kept = exclude_terminal_recovery_failures([formal_failed, formal_available, preprint])
        finally:
            pipeline_module.has_exhausted_recovery_paths = old_terminal

        self.assertEqual([item.paper.source_id for item in kept], ["available", "2601.00001"])

    def test_repeatedly_unavailable_venue_is_excluded_without_affecting_other_tiers(self):
        unavailable = ScoredPaper(
            Paper("crossref", "robotics", "Unavailable venue", "", [], date.today(), venue="Science Robotics", doi="10.1/robotics"),
            ScoreBreakdown(1, 1, 1, 1, 1, 1),
        )
        alternate = ScoredPaper(
            Paper("crossref", "nature", "Same Tier alternate", "", [], date.today(), venue="Nature", doi="10.1/nature"),
            ScoreBreakdown(1, 1, 1, 1, 1, 0.9),
        )
        preprint = ScoredPaper(
            Paper("arxiv", "2601.00001", "Preprint", "", [], date.today()),
            ScoreBreakdown(1, 1, 1, 1, 1, 0.8),
        )
        old_terminal = pipeline_module.has_exhausted_recovery_paths
        old_venues = pipeline_module.exhausted_recovery_venues
        old_imported = pipeline_module.has_imported_recovery
        try:
            pipeline_module.has_exhausted_recovery_paths = lambda _doi: False
            pipeline_module.exhausted_recovery_venues = lambda: {"science robotics"}
            pipeline_module.has_imported_recovery = lambda doi: doi == "10.1/robotics"
            kept = exclude_terminal_recovery_failures([unavailable, alternate, preprint])
        finally:
            pipeline_module.has_exhausted_recovery_paths = old_terminal
            pipeline_module.exhausted_recovery_venues = old_venues
            pipeline_module.has_imported_recovery = old_imported

        self.assertEqual([item.paper.source_id for item in kept], ["robotics", "nature", "2601.00001"])

    def test_dedupe_papers_by_title_and_doi(self):
        papers = [
            Paper("arxiv", "1", "Same Title", "a", [], date.today(), doi="10/x"),
            Paper("arxiv", "2", "Same Title", "b", [], date.today(), doi="10/x"),
            Paper("arxiv", "3", "Different Title", "c", [], date.today(), doi="10/y"),
        ]
        self.assertEqual(len(dedupe_papers(papers)), 2)

    def test_dedupe_papers_canonicalizes_doi_urls(self):
        papers = [
            Paper("crossref", "1", "Same Title", "a", [], date.today(), doi="10.1038/example"),
            Paper("openalex", "2", "Same Title", "b", [], date.today(), doi="https://doi.org/10.1038/example"),
        ]

        self.assertEqual(len(dedupe_papers(papers)), 1)

    def test_previous_recommendations_are_excluded_from_future_issues(self):
        papers = [
            Paper("crossref", "new", "New Paper", "a", [], date(2026, 5, 23), doi="10/new"),
            Paper("crossref", "old-doi", "Different Title", "a", [], date(2026, 5, 23), doi="10/old"),
            Paper("arxiv", "2601.00001v1", "Arxiv Old", "a", [], date(2026, 5, 23)),
            Paper("crossref", "old-title", "Previously Recommended Title", "a", [], date(2026, 5, 23)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            issues = Path(tmp) / "issues" / "2026-05-20"
            issues.mkdir(parents=True)
            (issues / "research_packet.json").write_text(
                json.dumps(
                    [
                        {"paper_id": "crossref:old", "doi": "https://doi.org/10/old", "title": "Old DOI Paper"},
                        {"paper_id": "arxiv:2601.00001v1", "title": "Arxiv Old"},
                        {"paper_id": "crossref:title", "title": "Previously Recommended Title"},
                    ]
                ),
                encoding="utf-8",
            )

            filtered = exclude_previously_recommended_papers(papers, date(2026, 5, 23), site_dir=tmp)

        self.assertEqual([paper.source_id for paper in filtered], ["new"])

    def test_previous_recommendations_support_object_packets_and_canonical_keys(self):
        papers = [
            Paper("crossref", "new", "New Paper", "a", [], date(2026, 5, 23), doi="10/new"),
            Paper("crossref", "doi-url", "Different DOI Title", "a", [], date(2026, 5, 23), doi="https://dx.doi.org/10/old"),
            Paper("arxiv", "2601.00001v2", "Changed Version", "a", [], date(2026, 5, 23)),
            Paper("crossref", "title", "Previously: Recommended Title!", "a", [], date(2026, 5, 23)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            issues = Path(tmp) / "issues" / "2026-05-20"
            issues.mkdir(parents=True)
            (issues / "research_packet.json").write_text(
                json.dumps(
                    {
                        "papers": [
                            {"paper_id": "crossref:old", "doi": "doi:10/old", "title": "Old DOI Paper"},
                            {"paper_id": "arxiv:2601.00001v1", "title": "Arxiv Old"},
                            {"paper_id": "crossref:title-old", "title": "Previously Recommended Title"},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            filtered = exclude_previously_recommended_papers(papers, date(2026, 5, 23), site_dir=tmp)

        self.assertEqual([paper.source_id for paper in filtered], ["new"])

    def test_same_day_recommendations_are_not_excluded(self):
        papers = [Paper("crossref", "same", "Same Day Paper", "a", [], date(2026, 5, 23), doi="10/same")]
        with tempfile.TemporaryDirectory() as tmp:
            issues = Path(tmp) / "issues" / "2026-05-23"
            issues.mkdir(parents=True)
            (issues / "research_packet.json").write_text(
                json.dumps([{"paper_id": "crossref:same", "doi": "10/same", "title": "Same Day Paper"}]),
                encoding="utf-8",
            )

            filtered = exclude_previously_recommended_papers(papers, date(2026, 5, 23), site_dir=tmp)

        self.assertEqual([paper.source_id for paper in filtered], ["same"])

    def test_history_check_emits_compact_report_and_cached_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site"
            issue = site / "issues" / "2026-05-20"
            issue.mkdir(parents=True)
            issue.joinpath("research_packet.json").write_text(
                json.dumps(
                    [
                        {
                            "paper_id": "crossref:old",
                            "doi": "10.1000/old",
                            "title": "Previously Recommended Robot Paper",
                            "fulltext": "private historical full text " * 1000,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            current = Path(tmp) / "current.json"
            current.write_text(
                json.dumps(
                    [
                        {"paper_id": "crossref:new", "doi": "10.1000/new", "title": "New Paper"},
                        {"paper_id": "crossref:old", "doi": "10.1000/old", "title": "Old Paper"},
                    ]
                ),
                encoding="utf-8",
            )

            report = check_research_packet_history(
                str(current),
                today=date(2026, 5, 23),
                site_dir=str(site),
            )

            self.assertEqual(report["paper_count"], 2)
            self.assertEqual(report["overlap_count"], 1)
            self.assertEqual(report["overlaps"][0]["paper_id"], "crossref:old")
            index = site / ".recommendation_history_index.json"
            self.assertTrue(index.exists())
            self.assertNotIn("private historical full text", index.read_text(encoding="utf-8"))

    def test_run_date_can_be_overridden_for_test_issues(self):
        old = os.environ.get("PAPER_RADAR_RUN_DATE")
        os.environ["PAPER_RADAR_RUN_DATE"] = "2026-05-26"
        try:
            self.assertEqual(current_run_date(), date(2026, 5, 26))
        finally:
            if old is None:
                os.environ.pop("PAPER_RADAR_RUN_DATE", None)
            else:
                os.environ["PAPER_RADAR_RUN_DATE"] = old

    def test_required_figures_drop_empty_items(self):
        profile = {
            "selection": {
                "target_count": 2,
                "direct_min": 0,
                "direct_max": 2,
                "official_min": 1,
                "require_verified_figure_one": True,
            }
        }
        papers = [
            Paper("crossref", "no-fig", "Formal no figure", "a", [], date.today(), key_figure_path=""),
            Paper("arxiv", "fig-1", "Has figure 1", "b", [], date.today(), key_figure_path="fig1.png"),
            Paper("arxiv", "fig-2", "Has figure 2", "c", [], date.today(), key_figure_path="fig2.png"),
        ]
        scored = [
            ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1 - idx * 0.1))
            for idx, paper in enumerate(papers)
        ]

        old_materialize = pipeline_module.materialize_key_figures
        old_hydrate = pipeline_module.hydrate_fulltexts
        old_audit = pipeline_module.figure_one_audit
        old_working_set = os.environ.get("PAPER_RADAR_WORKING_SET_PATH")
        try:
            def fake_materialize(items, _paper_id_fn):
                item = items[0]
                if item.paper.source_id != "no-fig":
                    item.paper.key_figure_path = f"output/figures/{item.paper.source_id}.png"

            pipeline_module.materialize_key_figures = fake_materialize
            pipeline_module.hydrate_fulltexts = lambda items: [
                setattr(item.paper, "fulltext", "x" * 1000) for item in items
            ]
            pipeline_module.figure_one_audit = lambda paper: {
                "accepted": paper.source_id in {"fig-1", "fig-2"}
            }
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = str(Path(tmp) / "working.json")
                selected = select_digest_requiring_key_figures(scored, profile)
        finally:
            pipeline_module.materialize_key_figures = old_materialize
            pipeline_module.hydrate_fulltexts = old_hydrate
            pipeline_module.figure_one_audit = old_audit
            if old_working_set is None:
                os.environ.pop("PAPER_RADAR_WORKING_SET_PATH", None)
            else:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = old_working_set

        # The provisional formal slot cannot be silently replaced with an
        # extra preprint just because its Figure 1 is unavailable. It must be
        # recovered or replaced by a same-tier formal candidate on a later run.
        self.assertEqual([item.paper.source_id for item in selected], ["fig-1"])

    def test_completed_working_set_slot_replaces_only_same_tier(self):
        profile = {
            "selection": {"target_count": 3},
            "tier1_broad_venues": ["Science Robotics"],
            "preferred_venues": ["Nature Communications"],
        }
        selected = [
            ScoredPaper(Paper("crossref", "t1-new", "new t1", "", [], date.today(), venue="Science Robotics", doi="10/t1-new"), ScoreBreakdown(1, 1, 1, 1, 1, 1)),
            ScoredPaper(Paper("crossref", "t2-new", "new t2", "", [], date.today(), venue="Nature Communications", doi="10/t2-new"), ScoreBreakdown(1, 1, 1, 1, 1, 1)),
            ScoredPaper(Paper("arxiv", "a-new", "new arxiv", "", [], date.today()), ScoreBreakdown(1, 1, 1, 1, 1, 1)),
        ]
        frozen = ScoredPaper(Paper("crossref", "t2-old", "frozen t2", "", [], date.today(), venue="Nature Communications", doi="10/t2-old"), ScoreBreakdown(1, 1, 1, 1, 1, 1))
        with tempfile.TemporaryDirectory() as tmp:
            figure = Path(tmp) / "fig1.png"
            figure.write_bytes(b"figure")
            working = Path(tmp) / "working.json"
            working.write_text(json.dumps({"issue_date": date.today().isoformat(), "papers": [{"identity": "10/t2-old", "state": "complete", "bucket": "tier2", "key_figure_path": str(figure)}]}), encoding="utf-8")
            old_path = os.environ.get("PAPER_RADAR_WORKING_SET_PATH")
            os.environ["PAPER_RADAR_WORKING_SET_PATH"] = str(working)
            try:
                result = apply_frozen_issue_slots(selected, selected + [frozen], profile)
            finally:
                if old_path is None:
                    os.environ.pop("PAPER_RADAR_WORKING_SET_PATH", None)
                else:
                    os.environ["PAPER_RADAR_WORKING_SET_PATH"] = old_path

        self.assertEqual([item.paper.source_id for item in result], ["t1-new", "t2-old", "a-new"])

    def test_figure_candidate_pool_keeps_formal_candidates(self):
        profile = {
            "selection": {
                "target_count": 10,
                "figure_candidate_pool": 10,
                "official_figure_candidate_pool": 12,
                "preprint_figure_candidate_pool": 4,
                "preprint_max": 3,
            }
        }
        scored = []
        for idx in range(20):
            paper = Paper("arxiv", f"a{idx}", f"arxiv {idx}", "a", [], date.today())
            scored.append(ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1.0 - idx * 0.01)))
        for idx in range(6):
            paper = Paper("crossref", f"c{idx}", f"formal {idx}", "a", [], date.today(), doi=f"10/x/{idx}")
            scored.append(ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 0.5 - idx * 0.01)))
        for idx in range(6):
            paper = Paper("openalex", f"o{idx}", f"openalex formal {idx}", "a", [], date.today(), doi=f"10/oa/{idx}")
            scored.append(ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 0.5 - idx * 0.01)))

        pool = build_figure_candidate_pool(scored, profile)

        self.assertEqual(sum(1 for item in pool if item.paper.source in {"crossref", "openalex"}), 12)
        self.assertEqual(sum(1 for item in pool if item.paper.source == "arxiv"), 10)

    def test_figure_extraction_stops_once_sufficient_figure_coverage_exists(self):
        today = date.today()
        scored = [
            ScoredPaper(Paper("crossref", f"c{idx}", f"formal {idx}", "a", [], today, doi=f"10/x/{idx}"), ScoreBreakdown(1, 1, 1, 1, 1, 1 - idx * 0.01))
            for idx in range(4)
        ]
        profile = {
            "selection": {
                "target_count": 2,
                "direct_min": 0,
                "direct_max": 2,
                "official_min": 2,
                "preprint_max": 0,
                "tier2_min": 0,
                "preprint_min": 0,
                "require_verified_figure_one": True,
            },
            "preferred_venues": [],
        }

        old_pool = pipeline_module.build_figure_candidate_pool
        old_hydrate = pipeline_module.hydrate_fulltexts
        old_materialize = pipeline_module.materialize_key_figures
        old_audit = pipeline_module.figure_one_audit
        old_working_set = os.environ.get("PAPER_RADAR_WORKING_SET_PATH")
        try:
            seen = []
            pipeline_module.build_figure_candidate_pool = lambda *_args, **_kwargs: scored
            pipeline_module.hydrate_fulltexts = lambda items: [
                setattr(item.paper, "fulltext", "x" * 1000) for item in items
            ]
            pipeline_module.figure_one_audit = lambda paper: {"accepted": bool(paper.key_figure_path)}

            def fake_materialize(items, _paper_id_fn):
                item = items[0]
                seen.append(item.paper.source_id)
                item.paper.key_figure_path = f"output/figures/{item.paper.source_id}.png"
                return {}

            pipeline_module.materialize_key_figures = fake_materialize

            with tempfile.TemporaryDirectory() as tmp:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = str(Path(tmp) / "working.json")
                selected = select_digest_requiring_key_figures(scored, profile)

            self.assertEqual(len(selected), 2)
            self.assertEqual(seen, ["c0", "c1"])
        finally:
            pipeline_module.build_figure_candidate_pool = old_pool
            pipeline_module.hydrate_fulltexts = old_hydrate
            pipeline_module.materialize_key_figures = old_materialize
            pipeline_module.figure_one_audit = old_audit
            if old_working_set is None:
                os.environ.pop("PAPER_RADAR_WORKING_SET_PATH", None)
            else:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = old_working_set

    def test_post_recovery_download_scan_can_complete_a_late_selected_pdf(self):
        paper = Paper(
            "crossref",
            "late",
            "Late publisher PDF",
            "a",
            [],
            date.today(),
            doi="10/x/late",
        )
        scored = [ScoredPaper(paper, ScoreBreakdown(1, 1, 1, 1, 1, 1))]
        profile = {
            "selection": {
                "target_count": 1,
                "direct_min": 0,
                "direct_max": 1,
                "official_min": 1,
                "preprint_max": 0,
                "tier2_min": 0,
                "preprint_min": 0,
                "require_verified_figure_one": True,
            },
            "preferred_venues": [],
        }

        old_hydrate = pipeline_module.hydrate_fulltexts
        old_materialize = pipeline_module.materialize_key_figures
        old_audit = pipeline_module.figure_one_audit
        old_ingest = pipeline_module.ingest_recent_downloads
        old_working_set = os.environ.get("PAPER_RADAR_WORKING_SET_PATH")
        try:
            labels = []
            pipeline_module.hydrate_fulltexts = lambda items: [
                setattr(item.paper, "fulltext", "x" * 1000) for item in items
            ]
            pipeline_module.materialize_key_figures = lambda *_args, **_kwargs: None
            pipeline_module.figure_one_audit = lambda item: {
                "accepted": bool(item.key_figure_path)
            }

            def fake_ingest(items, scan_label="intake"):
                labels.append(scan_label)
                items[0].paper.key_figure_path = "output/figures/late.png"
                items[0].paper.key_figure_caption = "Fig. 1 | Late arrival"
                return [{"paper_id": "10/x/late"}]

            pipeline_module.ingest_recent_downloads = fake_ingest
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = str(Path(tmp) / "working.json")
                selected = select_digest_requiring_key_figures(
                    scored,
                    profile,
                    reconcile_downloads=True,
                )
        finally:
            pipeline_module.hydrate_fulltexts = old_hydrate
            pipeline_module.materialize_key_figures = old_materialize
            pipeline_module.figure_one_audit = old_audit
            pipeline_module.ingest_recent_downloads = old_ingest
            if old_working_set is None:
                os.environ.pop("PAPER_RADAR_WORKING_SET_PATH", None)
            else:
                os.environ["PAPER_RADAR_WORKING_SET_PATH"] = old_working_set

        self.assertEqual(labels, ["post_recovery"])
        self.assertEqual(selected, scored)

    def test_incomplete_live_selection_preserves_existing_complete_packet(self):
        profile = {"selection": {"target_count": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output/figures").mkdir(parents=True)
                Path("output/figures/a.png").write_bytes(b"a")
                Path("output/figures/b.png").write_bytes(b"b")
                Path("output/research_packet.json").write_text(
                    json.dumps(
                        [
                            {"paper_id": "a", "key_figure_path": "output/figures/a.png"},
                            {"paper_id": "b", "key_figure_path": "output/figures/b.png"},
                        ]
                    ),
                    encoding="utf-8",
                )

                self.assertTrue(has_complete_research_packet("output/research_packet.json", 2))
                self.assertTrue(preserve_existing_complete_packet_when_incomplete([], profile))
                self.assertEqual(len(json.loads(Path("output/research_packet.json").read_text())), 2)
            finally:
                os.chdir(old)

    def test_preserved_packet_status_is_not_publishable(self):
        profile = {"selection": {"target_count": 2}}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output").mkdir()
                Path("output/research_packet.json").write_text(
                    json.dumps(
                        [
                            {"paper_id": "a", "key_figure_path": "output/figures/a.png"},
                            {"paper_id": "b", "key_figure_path": "output/figures/b.png"},
                        ]
                    ),
                    encoding="utf-8",
                )

                write_prepare_status([], profile, preserved_existing_packet=True)

                status = json.loads(Path("output/prepare_status.json").read_text(encoding="utf-8"))
                self.assertTrue(status["preserved_existing_packet"])
                self.assertFalse(status["ready_to_publish"])
            finally:
                os.chdir(old)

    def test_prepare_status_rejects_packet_without_fulltext(self):
        profile = {"selection": {"target_count": 1}}
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output/figures").mkdir(parents=True)
                Path("output/figures/a.png").write_bytes(b"a")
                Path("output/research_packet.json").write_text(
                    json.dumps(
                        [{"paper_id": "a", "fulltext": "", "key_figure_path": "output/figures/a.png"}]
                    ),
                    encoding="utf-8",
                )

                write_prepare_status([object()], profile, preserved_existing_packet=False)

                status = json.loads(Path("output/prepare_status.json").read_text(encoding="utf-8"))
                self.assertFalse(status["ready_to_publish"])
                self.assertEqual(status["quality_gate"]["figure_count"], 1)
                self.assertEqual(status["quality_gate"]["fulltext_count"], 0)
            finally:
                os.chdir(old)

    def test_prepare_status_reports_figure_identity_after_metadata_selection(self):
        profile = {
            "selection": {
                "target_count": 1,
                "semantic_audit_required": True,
                "require_verified_figure_one": True,
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output").mkdir()

                write_prepare_status(
                    [],
                    profile,
                    preserved_existing_packet=False,
                    pre_figure_selection_count=1,
                )

                status = json.loads(Path("output/prepare_status.json").read_text(encoding="utf-8"))
                self.assertEqual(status["blocked_reason"], "figure_one_identity_failed")
                self.assertEqual(status["pre_figure_selection_count"], 1)
            finally:
                os.chdir(old)

    def test_live_candidate_fetch_uses_venue_and_query_passes_not_cross_product(self):
        from paper_radar.pipeline import crossref

        calls = []
        old_probe = pipeline_module.probe_url
        old_doi = crossref.fetch_work_by_doi
        old_venue = crossref.fetch_recent_journal_works
        old_query = crossref.fetch_recent_query
        try:
            pipeline_module.probe_url = lambda *_args, **_kwargs: 200
            crossref.fetch_work_by_doi = lambda doi, *_args, **_kwargs: calls.append(("doi", doi)) or Paper("crossref", doi, "doi paper", "a", [], date.today(), doi=doi)
            crossref.fetch_recent_journal_works = lambda venue, *_args, **_kwargs: calls.append(("venue", venue)) or []
            crossref.fetch_recent_query = lambda query, *_args, **_kwargs: calls.append(("query", query)) or []
            profile = {
                "selection": {
                    "backfill_days": 90,
                    "official_backfill_days": 180,
                    "max_authority_results_per_query": 10,
                    "max_authority_results_per_venue": 80,
                    "max_results_per_query": 10,
                },
                "must_watch_dois": ["10.1038/s41586-026-10461-3"],
                "venue_watchlist": ["Nature", "MobiCom"],
                "authority_queries": ["robot perception", "mobile sensing"],
                "queries": {"arxiv": [], "openalex": []},
            }

            fetch_live_candidates(profile, date.today())

            self.assertEqual(
                calls,
                [
                    ("doi", "10.1038/s41586-026-10461-3"),
                    ("venue", "Nature"),
                    ("venue", "MobiCom"),
                    ("query", "robot perception"),
                    ("query", "mobile sensing"),
                ],
            )
        finally:
            pipeline_module.probe_url = old_probe
            crossref.fetch_work_by_doi = old_doi
            crossref.fetch_recent_journal_works = old_venue
            crossref.fetch_recent_query = old_query

    def test_tier1_venue_recall_uses_its_configured_three_year_depth(self):
        profile = {
            "tier1_broad_venues": ["Nature", "Science Robotics"],
            "selection": {
                "max_authority_results_per_venue": 80,
                "max_tier1_authority_results_per_venue": 360,
            },
        }

        self.assertEqual(pipeline_module._venue_recall_limit(profile, "Nature"), 360)
        self.assertEqual(pipeline_module._venue_recall_limit(profile, "Science Robotics"), 360)
        self.assertEqual(pipeline_module._venue_recall_limit(profile, "Nature Sensors"), 80)

    def test_live_candidate_fetch_runs_configured_tier2_targeted_journal_queries(self):
        from paper_radar.pipeline import crossref

        calls = []
        old_probe = pipeline_module.probe_url
        old_venue = crossref.fetch_recent_journal_works
        old_journal_query = crossref.fetch_recent_journal_query
        old_query = crossref.fetch_recent_query
        try:
            pipeline_module.probe_url = lambda *_args, **_kwargs: 200
            crossref.fetch_recent_journal_works = lambda *_args, **_kwargs: []
            crossref.fetch_recent_journal_query = lambda venue, query, *_args, **_kwargs: calls.append((venue, query)) or []
            crossref.fetch_recent_query = lambda *_args, **_kwargs: []
            profile = {
                "selection": {
                    "backfill_days": 90,
                    "official_backfill_days": 180,
                    "max_authority_results_per_query": 10,
                    "max_authority_results_per_venue": 80,
                    "max_tier2_targeted_results_per_query": 50,
                },
                "venue_watchlist": ["Nature"],
                "tier2_targeted_recall_venues": ["Nature Communications"],
                "tier2_targeted_recall_queries": ["robot", "tactile sensing"],
                "authority_queries": [],
                "queries": {"arxiv": [], "openalex": []},
            }

            fetch_live_candidates(profile, date.today())

            self.assertEqual(calls, [("Nature Communications", "robot"), ("Nature Communications", "tactile sensing")])
        finally:
            pipeline_module.probe_url = old_probe
            crossref.fetch_recent_journal_works = old_venue
            crossref.fetch_recent_journal_query = old_journal_query
            crossref.fetch_recent_query = old_query

    def test_live_candidate_fetch_uses_publisher_toc_as_doi_recall(self):
        from paper_radar.pipeline import crossref, publisher_toc

        calls = []
        old_probe = pipeline_module.probe_url
        old_toc = publisher_toc.fetch_candidate_dois
        old_doi = crossref.fetch_work_by_doi
        old_venue = crossref.fetch_recent_journal_works
        old_query = crossref.fetch_recent_query
        try:
            pipeline_module.probe_url = lambda *_args, **_kwargs: 200
            publisher_toc.fetch_candidate_dois = (
                lambda *_args, **_kwargs: [
                    publisher_toc.TocCandidate(
                        doi="10.1038/s41586-026-10461-3",
                        title="Efficient robot navigation inspired by honeybee learning flights",
                        venue="Nature",
                        published_at=date(2026, 5, 13),
                        url="https://www.nature.com/articles/s41586-026-10461-3",
                    )
                ]
            )
            crossref.fetch_work_by_doi = (
                lambda doi, *_args, **_kwargs: calls.append(("doi", doi))
                or Paper("crossref", doi, "doi paper", "a", [], date.today(), doi=doi)
            )
            crossref.fetch_recent_journal_works = lambda venue, *_args, **_kwargs: calls.append(("venue", venue)) or []
            crossref.fetch_recent_query = lambda query, *_args, **_kwargs: calls.append(("query", query)) or []
            profile = {
                "selection": {
                    "backfill_days": 90,
                    "official_backfill_days": 180,
                    "max_authority_results_per_query": 10,
                    "max_authority_results_per_venue": 80,
                    "max_results_per_query": 10,
                    "max_publisher_toc_items_per_feed": 20,
                    "publisher_toc_timeout_seconds": 5,
                },
                "publisher_toc_feeds": [{"name": "Nature", "url": "https://www.nature.com/nature.rss"}],
                "publisher_toc_signals": ["robot navigation", "honeybee"],
                "must_watch_dois": [],
                "venue_watchlist": [],
                "authority_queries": [],
                "queries": {"arxiv": [], "openalex": []},
            }

            papers, _status = fetch_live_candidates(profile, date.today())

            self.assertEqual(calls, [("doi", "10.1038/s41586-026-10461-3")])
            self.assertEqual(papers[0].doi, "10.1038/s41586-026-10461-3")
        finally:
            pipeline_module.probe_url = old_probe
            publisher_toc.fetch_candidate_dois = old_toc
            crossref.fetch_work_by_doi = old_doi
            crossref.fetch_recent_journal_works = old_venue
            crossref.fetch_recent_query = old_query

    def test_source_probe_short_circuits_when_all_sources_are_down(self):
        old_probe = pipeline_module.probe_url
        old_sleep = pipeline_module.time.sleep
        old_venue = pipeline_module.crossref.fetch_recent_journal_works
        old_query = pipeline_module.crossref.fetch_recent_query
        old_openalex = pipeline_module.openalex.fetch_recent
        old_arxiv = pipeline_module.arxiv.fetch_recent
        old_retries = os.environ.get("PAPER_RADAR_SOURCE_PROBE_RETRIES")
        os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = "1"
        calls = []
        try:
            pipeline_module.probe_url = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("dns failed"))
            pipeline_module.time.sleep = lambda *_args, **_kwargs: None
            pipeline_module.crossref.fetch_recent_journal_works = lambda *_args, **_kwargs: calls.append("crossref-venue") or []
            pipeline_module.crossref.fetch_recent_query = lambda *_args, **_kwargs: calls.append("crossref-query") or []
            pipeline_module.openalex.fetch_recent = lambda *_args, **_kwargs: calls.append("openalex") or []
            pipeline_module.arxiv.fetch_recent = lambda *_args, **_kwargs: calls.append("arxiv") or []
            profile = {
                "selection": {
                    "backfill_days": 90,
                    "official_backfill_days": 180,
                    "max_authority_results_per_query": 10,
                    "max_authority_results_per_venue": 80,
                    "max_results_per_query": 10,
                },
                "venue_watchlist": ["Nature"],
                "authority_queries": ["robot perception"],
                "queries": {"arxiv": ["robot"], "openalex": ["robot"]},
            }
            with tempfile.TemporaryDirectory() as tmp:
                old = os.getcwd()
                os.chdir(tmp)
                try:
                    papers, status = fetch_live_candidates(profile, date.today())
                    self.assertEqual(papers, [])
                    self.assertTrue(all_configured_sources_unreachable(status))
                    self.assertEqual(calls, [])
                    status_path = Path(
                        os.environ.get("PAPER_RADAR_SOURCE_STATUS_PATH")
                        or "output/source_status.json"
                    )
                    written = json.loads(status_path.read_text(encoding="utf-8"))
                    self.assertFalse(written["crossref"]["ok"])
                    self.assertFalse(written["openalex"]["ok"])
                    self.assertFalse(written["arxiv"]["ok"])
                finally:
                    os.chdir(old)
        finally:
            pipeline_module.probe_url = old_probe
            pipeline_module.time.sleep = old_sleep
            pipeline_module.crossref.fetch_recent_journal_works = old_venue
            pipeline_module.crossref.fetch_recent_query = old_query
            pipeline_module.openalex.fetch_recent = old_openalex
            pipeline_module.arxiv.fetch_recent = old_arxiv
            if old_retries is None:
                os.environ.pop("PAPER_RADAR_SOURCE_PROBE_RETRIES", None)
            else:
                os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = old_retries

    def test_source_probe_skips_only_down_sources(self):
        old_probe = pipeline_module.probe_url
        old_retries = os.environ.get("PAPER_RADAR_SOURCE_PROBE_RETRIES")
        old_venue = pipeline_module.crossref.fetch_recent_journal_works
        old_query = pipeline_module.crossref.fetch_recent_query
        old_openalex = pipeline_module.openalex.fetch_recent
        old_arxiv = pipeline_module.arxiv.fetch_recent
        old_source_status = os.environ.get("PAPER_RADAR_SOURCE_STATUS_PATH")
        today = date.today()
        openalex_paper = Paper("openalex", "oa-1", "OpenAlex Paper", "a", [], today, doi="10/oa")
        arxiv_paper = Paper("arxiv", "ax-1", "arXiv Paper", "a", [], today)
        calls = []
        try:
            os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = "1"
            def fake_probe(url, *_args, **_kwargs):
                if "crossref" in url:
                    raise OSError("dns failed")
                return 200

            pipeline_module.probe_url = fake_probe
            pipeline_module.crossref.fetch_recent_journal_works = lambda *_args, **_kwargs: calls.append("crossref-venue") or []
            pipeline_module.crossref.fetch_recent_query = lambda *_args, **_kwargs: calls.append("crossref-query") or []
            pipeline_module.openalex.fetch_recent = lambda *_args, **_kwargs: calls.append("openalex") or [openalex_paper]
            pipeline_module.arxiv.fetch_recent = lambda *_args, **_kwargs: calls.append("arxiv") or [arxiv_paper]
            profile = {
                "selection": {
                    "backfill_days": 90,
                    "official_backfill_days": 180,
                    "max_authority_results_per_query": 10,
                    "max_authority_results_per_venue": 80,
                    "max_results_per_query": 10,
                },
                "venue_watchlist": ["Nature"],
                "authority_queries": ["robot perception"],
                "queries": {"arxiv": ["robot"], "openalex": ["robot"]},
            }

            with tempfile.TemporaryDirectory() as tmp:
                os.environ["PAPER_RADAR_SOURCE_STATUS_PATH"] = str(Path(tmp) / "source-status.json")
                papers, status = fetch_live_candidates(profile, today)

            self.assertEqual([paper.title for paper in papers], ["OpenAlex Paper", "arXiv Paper"])
            self.assertFalse(status["crossref"]["ok"])
            self.assertTrue(status["openalex"]["ok"])
            self.assertTrue(status["arxiv"]["ok"])
            self.assertEqual(calls, ["openalex", "arxiv"])
        finally:
            pipeline_module.probe_url = old_probe
            pipeline_module.crossref.fetch_recent_journal_works = old_venue
            pipeline_module.crossref.fetch_recent_query = old_query
            pipeline_module.openalex.fetch_recent = old_openalex
            pipeline_module.arxiv.fetch_recent = old_arxiv
            if old_source_status is None:
                os.environ.pop("PAPER_RADAR_SOURCE_STATUS_PATH", None)
            else:
                os.environ["PAPER_RADAR_SOURCE_STATUS_PATH"] = old_source_status
            if old_retries is None:
                os.environ.pop("PAPER_RADAR_SOURCE_PROBE_RETRIES", None)
            else:
                os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = old_retries

    def test_source_probe_retries_partially_down_sources(self):
        old_probe = pipeline_module.probe_url
        old_sleep = pipeline_module.time.sleep
        old_retries = os.environ.get("PAPER_RADAR_SOURCE_PROBE_RETRIES")
        attempts = {"arxiv": 0}
        try:
            os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = "2"
            pipeline_module.time.sleep = lambda *_args, **_kwargs: None

            def fake_probe(url, *_args, **_kwargs):
                if "export.arxiv.org" in url:
                    attempts["arxiv"] += 1
                    if attempts["arxiv"] == 1:
                        raise OSError("timeout")
                return 200

            pipeline_module.probe_url = fake_probe
            profile = {
                "venue_watchlist": ["Nature"],
                "authority_queries": [],
                "publisher_toc_feeds": [{"name": "Nature", "url": "https://www.nature.com/nature.rss"}],
                "queries": {"arxiv": ["robot"], "openalex": ["robot"]},
            }

            status = pipeline_module.probe_live_sources(profile, date.today())

            self.assertEqual(attempts["arxiv"], 2)
            self.assertTrue(status["arxiv"]["ok"])
            self.assertEqual(status["arxiv"]["attempts"], 2)
        finally:
            pipeline_module.probe_url = old_probe
            pipeline_module.time.sleep = old_sleep
            if old_retries is None:
                os.environ.pop("PAPER_RADAR_SOURCE_PROBE_RETRIES", None)
            else:
                os.environ["PAPER_RADAR_SOURCE_PROBE_RETRIES"] = old_retries


if __name__ == "__main__":
    unittest.main()
