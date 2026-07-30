import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

import paper_radar.sources.crossref as crossref
from paper_radar.sources.crossref import _best_pdf_url, _clean_text, normalize_work
from paper_radar.publisher_assets import publisher_figure_candidates


class CrossrefTests(unittest.TestCase):
    def test_clean_text_removes_markup(self):
        self.assertEqual(_clean_text("<jats:p>Hello &amp; world</jats:p>"), "Hello & world")

    def test_crossref_pdf_link_is_kept(self):
        item = {
            "DOI": "10.1126/sciadv.test",
            "title": ["T"],
            "container-title": ["Science Advances"],
            "published": {"date-parts": [[2026, 5, 1]]},
            "link": [{"URL": "https://www.science.org/doi/pdf/10.1126/sciadv.test", "content-type": "unspecified"}],
        }

        self.assertEqual(_best_pdf_url(item), "https://www.science.org/doi/pdf/10.1126/sciadv.test")
        self.assertEqual(normalize_work(item).pdf_url, "https://www.science.org/doi/pdf/10.1126/sciadv.test")

    def test_nature_media_asset_url_is_predictable(self):
        paper = normalize_work(
            {
                "DOI": "10.1038/s41467-026-73216-8",
                "title": ["T"],
                "container-title": ["Nature Communications"],
                "published": {"date-parts": [[2026, 5, 16]]},
            }
        )

        urls = list(publisher_figure_candidates(paper))
        self.assertIn("41467_2026_73216_Fig1_HTML.png", urls[0])
        self.assertTrue(urls[0].startswith("https://media.springernature.com/lw685/"))

    def test_nature_media_asset_url_supports_letter_checksum_and_zero_padded_article_number(self):
        paper = normalize_work(
            {
                "DOI": "10.1038/s42256-025-00988-x",
                "title": ["T"],
                "container-title": ["Nature Machine Intelligence"],
                "published": {"date-parts": [[2025, 3, 17]]},
            }
        )

        urls = list(publisher_figure_candidates(paper))

        self.assertIn("42256_2025_988_Fig1_HTML.png", urls[0])

    def test_crossref_fetches_are_sorted_by_recent_publication(self):
        seen = []
        old_get_bytes = crossref.get_bytes
        try:
            def fake_get_bytes(url, *_args, **_kwargs):
                seen.append(url)
                return b'{"message":{"items":[]}}'

            crossref.get_bytes = fake_get_bytes
            crossref.fetch_recent_journal_works("Nature", date(2026, 1, 1), rows=10)
            crossref.fetch_recent_query("bioinspired robot navigation", date(2026, 1, 1), rows=10)

            self.assertTrue(all("sort=published" in url for url in seen))
            self.assertTrue(all("order=desc" in url for url in seen))
        finally:
            crossref.get_bytes = old_get_bytes

    def test_crossref_fetch_includes_until_publication_boundary(self):
        seen = []
        old_get_bytes = crossref.get_bytes
        try:
            crossref.get_bytes = lambda url, *_args, **_kwargs: seen.append(url) or b'{"message":{"items":[]}}'
            crossref.fetch_recent_query(
                "robot",
                date(2026, 1, 1),
                until_date=date(2026, 1, 31),
            )

            publication_filter = parse_qs(urlparse(seen[0]).query)["filter"][0]
            self.assertIn("from-pub-date:2026-01-01", publication_filter)
            self.assertIn("until-pub-date:2026-01-31", publication_filter)
        finally:
            crossref.get_bytes = old_get_bytes

    def test_crossref_can_fetch_must_watch_doi(self):
        seen = []
        old_get_bytes = crossref.get_bytes
        try:
            def fake_get_bytes(url, *_args, **_kwargs):
                seen.append(url)
                return (
                    b'{"message":{"DOI":"10.1038/s41586-026-10461-3",'
                    b'"title":["Efficient robot navigation inspired by honeybee learning flights"],'
                    b'"container-title":["Nature"],'
                    b'"published":{"date-parts":[[2026,5,28]]}}}'
                )

            crossref.get_bytes = fake_get_bytes
            paper = crossref.fetch_work_by_doi("10.1038/s41586-026-10461-3")

            self.assertEqual(paper.doi, "10.1038/s41586-026-10461-3")
            self.assertEqual(paper.venue, "Nature")
            self.assertIn("/works/10.1038/s41586-026-10461-3", seen[0])
        finally:
            crossref.get_bytes = old_get_bytes


if __name__ == "__main__":
    unittest.main()
