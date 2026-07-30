import unittest

from paper_radar.analysis import _extract_output_text


class OpenAIAnalysisTests(unittest.TestCase):
    def test_extract_output_text(self):
        payload = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"core_insight":"x"}',
                        }
                    ]
                }
            ]
        }
        self.assertEqual(_extract_output_text(payload), '{"core_insight":"x"}')


if __name__ == "__main__":
    unittest.main()

