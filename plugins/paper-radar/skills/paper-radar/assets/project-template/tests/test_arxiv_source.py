import unittest

import paper_radar.sources.arxiv as arxiv


class ArxivSourceTests(unittest.TestCase):
    def test_throttle_sleeps_when_requests_are_too_close(self):
        old_last = arxiv._LAST_REQUEST_TS
        old_monotonic = arxiv.time.monotonic
        old_sleep = arxiv.time.sleep
        try:
            sleeps = []
            arxiv._LAST_REQUEST_TS = 100.0
            arxiv.time.monotonic = lambda: 101.0
            arxiv.time.sleep = lambda seconds: sleeps.append(seconds)
            arxiv._throttle()
            self.assertEqual(sleeps, [2.0])
        finally:
            arxiv._LAST_REQUEST_TS = old_last
            arxiv.time.monotonic = old_monotonic
            arxiv.time.sleep = old_sleep


if __name__ == "__main__":
    unittest.main()
