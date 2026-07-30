import unittest

import requests

from paper_radar.http import _retry_delay_seconds


class HttpRetryTests(unittest.TestCase):
    def test_excessive_retry_after_does_not_sleep_the_weekly_run(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "30309"
        error = requests.HTTPError(response=response)

        self.assertIsNone(_retry_delay_seconds(error, 0))

    def test_short_retry_after_is_honored(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "12"
        error = requests.HTTPError(response=response)

        self.assertEqual(_retry_delay_seconds(error, 0), 12.0)


if __name__ == "__main__":
    unittest.main()
