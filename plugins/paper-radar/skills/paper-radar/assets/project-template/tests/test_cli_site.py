import os
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zipfile import ZipFile

from paper_radar.cli import (
    _ensure_publishable_prepare_status,
    _inline_figure_assets,
    _public_html_errors,
    _public_packet_errors,
    bundle_digest_assets,
    build_static_site,
    cleanup_candidates,
    resolve_run_issue_date,
    write_link_email,
)


class StaticSiteTests(unittest.TestCase):
    def test_weekly_issue_date_aliases_are_deterministic(self):
        wednesday = date(2026, 7, 15)
        sunday = date(2026, 7, 19)

        self.assertEqual(
            resolve_run_issue_date("upcoming-sunday", today=wednesday),
            sunday,
        )
        self.assertEqual(
            resolve_run_issue_date("upcoming-sunday", today=sunday),
            sunday,
        )
        self.assertEqual(
            resolve_run_issue_date("previous-sunday", today=wednesday),
            date(2026, 7, 12),
        )
        self.assertEqual(
            resolve_run_issue_date("previous-sunday", today=sunday),
            date(2026, 7, 12),
        )

    def test_build_static_site_writes_latest_and_permanent_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output/figures").mkdir(parents=True)
                Path("output/figures/key.png").write_bytes(b"fakepng")
                Path("output/figures/extra.png").write_bytes(b"extra")
                Path("output/digest.html").write_text(
                    '<html><head><title>Paper Radar · 2026-05-20</title></head>'
                    '<body><img src="figures/key.png"></body></html>',
                    encoding="utf-8",
                )
                Path("output/email.html").write_text("email", encoding="utf-8")
                Path("output/research_packet.json").write_text(
                    json.dumps([{"key_figure_path": "output/figures/key.png", "fulltext": "private fulltext"}]),
                    encoding="utf-8",
                )
                urls = build_static_site(issue_date=date(2026, 5, 20))
                self.assertEqual(urls["latest"], "site/latest")
                self.assertTrue(Path("site/latest/index.html").exists())
                self.assertTrue(Path("site/issues/2026-05-20/index.html").exists())
                self.assertTrue(Path("site/issues/index.html").exists())
                self.assertIn("figures/key.png", Path("site/latest/index.html").read_text(encoding="utf-8"))
                self.assertTrue(Path("site/latest/figures/key.png").exists())
                self.assertTrue(Path("site/issues/2026-05-20/figures/key.png").exists())
                self.assertFalse(Path("site/latest/figures/extra.png").exists())
                self.assertFalse(Path("site/issues/2026-05-20/figures/extra.png").exists())
                self.assertIn('"current_issue": "issues/2026-05-20/"', Path("site/manifest.json").read_text())
                self.assertIn('"archive": "issues/"', Path("site/manifest.json").read_text())
                public_packet = json.loads(Path("site/latest/research_packet.json").read_text(encoding="utf-8"))
                self.assertNotIn("fulltext", public_packet[0])
            finally:
                os.chdir(old)

    def test_history_index_lists_previous_issues_and_paper_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                previous = Path("site/issues/2026-05-17")
                previous.mkdir(parents=True)
                previous.joinpath("research_packet.json").write_text(
                    json.dumps(
                        [
                            {
                                "title": "Previous robot sensing paper",
                                "venue": "Science Robotics",
                                "source": "crossref",
                                "url": "https://example.test/previous",
                                "key_figure_path": "figures/old.png",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                Path("output/figures").mkdir(parents=True)
                Path("output/figures/key.png").write_bytes(b"fakepng")
                Path("output/digest.html").write_text(
                    '<html><head><title>Paper Radar · 2026-05-20</title></head>'
                    '<body><img src="figures/key.png"></body></html>',
                    encoding="utf-8",
                )
                Path("output/research_packet.json").write_text(
                    json.dumps(
                        [
                            {
                                "title": "Current tactile robot paper",
                                "venue": "Nature Communications",
                                "source": "openalex",
                                "url": "https://example.test/current",
                                "key_figure_path": "output/figures/key.png",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )

                build_static_site(issue_date=date(2026, 5, 20))

                history = Path("site/issues/index.html").read_text(encoding="utf-8")
                self.assertIn("Paper Radar · 2026-05-20", history)
                self.assertIn("Paper Radar · 2026-05-17", history)
                self.assertLess(history.index("2026-05-20"), history.index("2026-05-17"))
                self.assertIn("https://example.test/current", history)
                self.assertIn("https://example.test/previous", history)
                self.assertIn("Nature Communications", history)
                self.assertIn("Science Robotics", history)
            finally:
                os.chdir(old)

    def test_link_email_uses_permanent_link_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "email-link.html"
            write_link_email(
                "https://example.test/issues/2026-05-20/",
                output_path=str(path),
                latest_url="https://example.test/latest/",
                archive_url="https://example.test/issues/",
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("不会被下一期覆盖", text)
            self.assertIn("https://example.test/issues/2026-05-20/", text)
            self.assertIn("https://example.test/latest/", text)
            self.assertIn("往期历史推荐", text)
            self.assertIn("https://example.test/issues/", text)

    def test_bundle_digest_assets_defaults_to_referenced_figures_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                Path("output/figures").mkdir(parents=True)
                Path("output/figures/key.png").write_bytes(b"key")
                Path("output/figures/extra.png").write_bytes(b"extra")
                Path("output/digest.html").write_text("digest", encoding="utf-8")
                Path("output/research_packet.json").write_text(
                    json.dumps([{"key_figure_path": "output/figures/key.png"}]),
                    encoding="utf-8",
                )

                bundle_digest_assets("output/bundle.zip")

                with ZipFile("output/bundle.zip") as zf:
                    names = set(zf.namelist())
                self.assertIn("figures/key.png", names)
                self.assertNotIn("figures/extra.png", names)
            finally:
                os.chdir(old)

    def test_public_packet_errors_detect_stale_payload(self):
        errors = _public_packet_errors(
            "latest_packet",
            200,
            [{"title": "Old", "key_figure_path": "fig.png", "fulltext": "private"}],
            ["New"],
            1,
        )

        self.assertIn("titles do not match local issue packet", " ".join(errors))
        self.assertIn("public packet still exposes fulltext", " ".join(errors))

    def test_public_html_errors_require_markers(self):
        errors = _public_html_errors("issue_html", 200, "Paper Radar · Journal Edition --paper", date(2026, 5, 20))

        self.assertIn('issue_html: missing id="archive-link"', errors)
        self.assertIn("issue_html: missing 历史推荐", errors)
        self.assertIn("issue_html: missing 2026-05-20", errors)

    def test_build_gate_rejects_preserved_previous_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "prepare_status.json"
            status.write_text(
                '{"target_count": 10, "live_selection_count": 8, '
                '"packet_count": 10, "preserved_existing_packet": true, "ready_to_publish": false}',
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as ctx:
                _ensure_publishable_prepare_status(str(status))

            self.assertIn("preserved previous packet", str(ctx.exception))

    def test_build_gate_rejects_missing_prepare_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "prepare_status.json"

            with self.assertRaises(SystemExit) as ctx:
                _ensure_publishable_prepare_status(str(missing))

            self.assertIn("Run prepare-weekly first", str(ctx.exception))

    def test_cleanup_candidates_only_include_old_transient_output_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output"
            root.mkdir()
            old_pages = root / "paper-pages"
            old_images = root / "paper-images"
            old_bundle = root / "paper-radar-digest.zip"
            keep_current = root / "research_packet.json"
            keep_figures = root / "figures"
            keep_recent = root / "recent-pages"
            for path in [old_pages, old_images, keep_figures, keep_recent]:
                path.mkdir()
            old_bundle.write_bytes(b"zip")
            keep_current.write_text("{}", encoding="utf-8")

            old_time = 1_000_000
            recent_time = 2_000_000
            for path in [old_pages, old_images, old_bundle]:
                os.utime(path, (old_time, old_time))
            for path in [keep_current, keep_figures, keep_recent]:
                os.utime(path, (recent_time, recent_time))

            candidates = cleanup_candidates(older_than_days=1, output_dir=str(root), now=recent_time)

            self.assertEqual(set(candidates), {old_pages, old_images, old_bundle})


if __name__ == "__main__":
    unittest.main()
