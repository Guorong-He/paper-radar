import unittest
from datetime import date

from paper_radar.models import Paper
from paper_radar.tagging import enrich_papers, passes_candidate_filter


class TaggingTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "robot_type_keywords": {"soft_robot": ["soft robot"]},
            "direct_signal_keywords": ["robot", "soft robot"],
            "transferable_signal_keywords": ["perception", "sensing"],
            "required_signal_groups": {
                "platform": ["robot"],
                "perception": ["perception", "sensing"],
            },
            "exclude_keywords": ["medical image segmentation"],
        }

    def test_direct_paper_requires_platform_and_perception(self):
        paper = Paper(
            source="fixture",
            source_id="1",
            title="Soft Robot Perception",
            abstract="A sensing method for a robot.",
            authors=[],
            published_at=date.today(),
        )
        enriched = enrich_papers([paper], self.profile)[0]
        self.assertEqual(enriched.paper_type, "direct")
        self.assertTrue(passes_candidate_filter(enriched, self.profile))

    def test_excluded_topic_is_filtered(self):
        paper = Paper(
            source="fixture",
            source_id="2",
            title="Medical Image Segmentation",
            abstract="A survey paper.",
            authors=[],
            published_at=date.today(),
        )
        enriched = enrich_papers([paper], self.profile)[0]
        self.assertFalse(passes_candidate_filter(enriched, self.profile))


if __name__ == "__main__":
    unittest.main()

