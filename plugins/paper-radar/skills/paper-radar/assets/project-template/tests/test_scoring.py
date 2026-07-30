import unittest
from datetime import date, timedelta

from paper_radar.models import Paper
from paper_radar.scoring import score_papers, freshness_score, user_priority_score


class FreshnessScoreTests(unittest.TestCase):
    def test_recent_paper_scores_high(self):
        profile = {"selection": {"fresh_days": 7, "lookback_days": 30}}
        paper = Paper(
            source="fixture",
            source_id="1",
            title="x",
            abstract="y",
            authors=[],
            published_at=date.today() - timedelta(days=3),
        )
        self.assertEqual(freshness_score(paper, date.today(), profile), 1.0)

    def test_old_paper_scores_low(self):
        profile = {"selection": {"fresh_days": 7, "lookback_days": 30}}
        paper = Paper(
            source="fixture",
            source_id="1",
            title="x",
            abstract="y",
            authors=[],
            published_at=date.today() - timedelta(days=45),
        )
        self.assertEqual(freshness_score(paper, date.today(), profile), 0.2)

    def test_user_priority_boost_prefers_flagship_robot_system_papers(self):
        profile = {
            "preferred_venues": ["Science Robotics", "Nature Communications"],
            "tier1_broad_venues": ["Science Robotics"],
            "preferred_authors": [],
            "preferred_labs": [],
            "relevance_keywords": ["robot", "locomotion"],
            "evidence_keywords": {"benchmark": [], "real_world": []},
            "user_priority_keywords": ["dynamic symmetry", "omnidirectional robot", "robot morphology"],
            "weights": {
                "venue_author_score": 0.3,
                "relevance_score": 0.25,
                "evidence_score": 0.2,
                "freshness_score": 0.15,
                "diversity_score": 0.1,
                "user_priority_score": 0.12,
            },
            "selection": {"fresh_days": 180, "lookback_days": 1095},
        }
        argus = Paper(
            source="crossref",
            source_id="10.1126/scirobotics.aec1725",
            title="Extreme dynamic symmetry enables omnidirectional and multifunctional robots",
            abstract="Argus uses robot morphology and dynamic isotropy for robust locomotion.",
            authors=[],
            venue="Science Robotics",
            published_at=date.today(),
        )
        ordinary = Paper(
            source="crossref",
            source_id="10.1126/scirobotics.other",
            title="A robot locomotion study",
            abstract="Robot locomotion in a flagship venue.",
            authors=[],
            venue="Science Robotics",
            published_at=date.today(),
        )

        self.assertEqual(user_priority_score(argus, profile), 1.0)
        self.assertEqual(user_priority_score(ordinary, profile), 0.0)
        scored = {item.paper.source_id: item.score.total_score for item in score_papers([argus, ordinary], profile, date.today())}

        self.assertGreater(scored["10.1126/scirobotics.aec1725"], scored["10.1126/scirobotics.other"])

    def test_user_priority_boost_is_smaller_for_preprints(self):
        profile = {
            "preferred_venues": ["Science Robotics"],
            "tier1_broad_venues": ["Science Robotics"],
            "user_priority_keywords": ["dynamic symmetry", "omnidirectional robot"],
        }
        formal = Paper(
            source="crossref",
            source_id="formal",
            title="Dynamic symmetry for an omnidirectional robot",
            abstract="",
            authors=[],
            venue="Science Robotics",
            published_at=date.today(),
        )
        preprint = Paper(
            source="arxiv",
            source_id="preprint",
            title="Dynamic symmetry for an omnidirectional robot",
            abstract="",
            authors=[],
            venue="arXiv",
            published_at=date.today(),
        )

        self.assertEqual(user_priority_score(formal, profile), 1.0)
        self.assertEqual(user_priority_score(preprint, profile), 0.25)


if __name__ == "__main__":
    unittest.main()
