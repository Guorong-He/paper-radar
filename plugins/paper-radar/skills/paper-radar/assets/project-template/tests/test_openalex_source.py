import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

import paper_radar.sources.openalex as openalex


class OpenAlexSourceTests(unittest.TestCase):
    def test_fetch_includes_upper_publication_boundary(self):
        seen = []
        old_get_bytes = openalex.get_bytes
        try:
            openalex.get_bytes = lambda url, *_args, **_kwargs: seen.append(url) or b'{"results":[]}'
            openalex.fetch_recent(
                "robot",
                date(2026, 1, 1),
                until_date=date(2026, 1, 31),
            )

            publication_filter = parse_qs(urlparse(seen[0]).query)["filter"][0]
            self.assertIn("from_publication_date:2026-01-01", publication_filter)
            self.assertIn("to_publication_date:2026-01-31", publication_filter)
        finally:
            openalex.get_bytes = old_get_bytes


if __name__ == "__main__":
    unittest.main()
