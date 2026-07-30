import unittest
from datetime import date

import paper_radar.sources.publisher_toc as publisher_toc


class PublisherTocTests(unittest.TestCase):
    def test_parse_nature_rdf_feed_extracts_research_doi(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <item rdf:about="https://www.nature.com/articles/s41586-026-10461-3">
    <title>Efficient robot navigation inspired by honeybee learning flights</title>
    <link>https://www.nature.com/articles/s41586-026-10461-3</link>
    <content:encoded>Nature, Published online: 13 May 2026; doi:10.1038/s41586-026-10461-3</content:encoded>
    <dc:date>2026-05-13</dc:date>
    <prism:publicationName>Nature</prism:publicationName>
    <prism:doi>10.1038/s41586-026-10461-3</prism:doi>
  </item>
</rdf:RDF>
"""

        candidates = publisher_toc.parse_feed(feed, default_venue="Nature")

        self.assertEqual(candidates[0].doi, "10.1038/s41586-026-10461-3")
        self.assertEqual(candidates[0].venue, "Nature")
        self.assertEqual(candidates[0].published_at, date(2026, 5, 13))

    def test_candidate_fetch_filters_news_and_requires_relevance_signal(self):
        old_get_bytes = publisher_toc.get_bytes
        try:
            def fake_get_bytes(*_args, **_kwargs):
                return b"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns="http://purl.org/rss/1.0/"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
  <item rdf:about="https://www.nature.com/articles/d41586-026-01911-z">
    <title>Robots in a news brief</title>
    <link>https://www.nature.com/articles/d41586-026-01911-z</link>
    <dc:date>2026-06-12</dc:date>
    <prism:publicationName>Nature</prism:publicationName>
    <prism:doi>10.1038/d41586-026-01911-z</prism:doi>
  </item>
  <item rdf:about="https://www.nature.com/articles/s41586-026-10461-3">
    <title>Efficient robot navigation inspired by honeybee learning flights</title>
    <link>https://www.nature.com/articles/s41586-026-10461-3</link>
    <dc:date>2026-05-13</dc:date>
    <prism:publicationName>Nature</prism:publicationName>
    <prism:doi>10.1038/s41586-026-10461-3</prism:doi>
  </item>
  <item rdf:about="https://www.nature.com/articles/s41586-026-00000-0">
    <title>A crystallographic study of unrelated matter</title>
    <link>https://www.nature.com/articles/s41586-026-00000-0</link>
    <dc:date>2026-05-13</dc:date>
    <prism:publicationName>Nature</prism:publicationName>
    <prism:doi>10.1038/s41586-026-00000-0</prism:doi>
  </item>
</rdf:RDF>
"""

            publisher_toc.get_bytes = fake_get_bytes
            candidates = publisher_toc.fetch_candidate_dois(
                [{"name": "Nature", "url": "https://www.nature.com/nature.rss"}],
                date(2026, 1, 1),
                ["robot navigation", "honeybee", "visual homing"],
            )

            self.assertEqual([candidate.doi for candidate in candidates], ["10.1038/s41586-026-10461-3"])
        finally:
            publisher_toc.get_bytes = old_get_bytes


if __name__ == "__main__":
    unittest.main()
