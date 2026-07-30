import unittest
from datetime import date
from unittest.mock import patch

from paper_radar.article_type import primary_research_audit
from paper_radar.models import Paper


class ArticleTypeTests(unittest.TestCase):
    def test_nature_news_and_views_is_rejected_from_landing_metadata(self):
        paper = Paper(
            source="crossref",
            source_id="10.1038/s44460-026-00050-2",
            title="Agentic simulation perfects perception of quadruped robots",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Nature Sensors",
            doi="10.1038/s44460-026-00050-2",
            url="https://www.nature.com/articles/s44460-026-00050-2",
        )
        with patch("paper_radar.article_type._nature_landing_content_type", return_value="news & views"):
            audit = primary_research_audit(paper)

        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["source"], "publisher_landing_metadata")

    def test_unknown_landing_type_does_not_discard_a_formal_paper(self):
        paper = Paper(
            source="crossref",
            source_id="10.1038/example",
            title="Robotic sensing research",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Nature Sensors",
            doi="10.1038/example",
            url="https://www.nature.com/articles/example",
        )
        with patch("paper_radar.article_type._nature_landing_content_type", return_value=""):
            self.assertTrue(primary_research_audit(paper)["accepted"])

    def test_nature_doi_uses_landing_metadata_when_source_url_is_a_doi_link(self):
        paper = Paper(
            source="crossref",
            source_id="10.1038/s44460-026-00050-2",
            title="Agentic simulation perfects perception of quadruped robots",
            abstract="",
            authors=[],
            published_at=date.today(),
            venue="Nature Sensors",
            doi="10.1038/s44460-026-00050-2",
            url="https://doi.org/10.1038/s44460-026-00050-2",
        )
        with patch("paper_radar.article_type._nature_landing_content_type", return_value="news & views") as lookup:
            audit = primary_research_audit(paper)

        self.assertFalse(audit["accepted"])
        self.assertEqual(lookup.call_args.args[0], "https://www.nature.com/articles/s44460-026-00050-2")
