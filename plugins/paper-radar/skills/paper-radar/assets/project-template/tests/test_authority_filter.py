import unittest
from datetime import date

from paper_radar.models import Paper
from paper_radar.tagging import candidate_audit, enrich_papers, passes_candidate_filter


class AuthorityFilterTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "robot_type_keywords": {"soft_robot": ["soft robot"], "manipulation": ["robot manipulation"]},
            "direct_signal_keywords": ["robot", "soft robot"],
            "transferable_signal_keywords": ["perception", "sensing", "tactile", "robot manipulation"],
            "required_signal_groups": {
                "platform": ["robot"],
                "perception": ["perception", "sensing", "tactile"],
                "robotics": ["robot manipulation", "locomotion", "planning"],
            },
            "strict_platform_keywords": ["robot", "robotic", "drone", "electronic skin"],
            "embodied_task_keywords": ["perception", "sensing", "tactile", "manipulation", "grasping", "planning", "control", "locomotion"],
            "exclude_keywords": ["quantum error correction", "seafloor", "seismogeodesy", "mid-ocean ridge", "tectonic", "human trajectory", "a survey", "perspective", "open challenges", "overview, robustness and challenges"],
            "preferred_venues": [
                "Nature Machine Intelligence",
                "Nature Sensors",
                "Science Robotics",
                "Nature Electronics",
                "Nature Communications",
                "Science Advances",
                "Device",
            ],
            "tier1_broad_venues": ["Nature Machine Intelligence", "Science Robotics", "Nature Electronics"],
            "broad_robotics_keywords": ["robot", "robot manipulation", "robot control"],
        }

    def test_crossref_requires_preferred_venue(self):
        paper = Paper(
            source="crossref",
            source_id="1",
            title="Robot tactile sensing",
            abstract="robot tactile sensing",
            authors=[],
            published_at=date.today(),
            venue="Unrelated Journal",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        self.assertFalse(passes_candidate_filter(enriched, self.profile))

    def test_high_venue_formal_robotics_paper_can_pass_without_perception(self):
        paper = Paper(
            source="crossref",
            source_id="2",
            title="Robot manipulation with reliable grasping",
            abstract="A robot manipulation method for grasping and planning.",
            authors=[],
            published_at=date.today(),
            venue="Science Robotics",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, self.profile))

    def test_second_tier_formal_robotics_paper_with_explicit_task_can_pass(self):
        paper = Paper(
            source="crossref",
            source_id="4",
            title="Robot manipulation with reliable grasping",
            abstract="A robot manipulation method for grasping and planning.",
            authors=[],
            published_at=date.today(),
            venue="Nature Communications",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, self.profile))

    def test_second_tier_formal_robot_perception_can_pass(self):
        paper = Paper(
            source="crossref",
            source_id="5",
            title="Robot tactile perception for grasping",
            abstract="A robot sensing method for tactile perception during grasping.",
            authors=[],
            published_at=date.today(),
            venue="Science Advances",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, self.profile))

    def test_second_tier_broad_robotics_toggle_cannot_relax_semantic_gate(self):
        self.profile["selection"] = {"tier2_broad_robotics_scope": True}
        paper = Paper(
            source="crossref",
            source_id="tier2-broad",
            title="Robot morphology study",
            abstract="An anatomical geometry study describes shape and material distributions.",
            authors=[],
            published_at=date.today(),
            venue="Nature Communications",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertFalse(passes_candidate_filter(enriched, self.profile))

    def test_arxiv_robotics_only_without_embodied_task_is_rejected(self):
        paper = Paper(
            source="arxiv",
            source_id="3",
            title="Robot morphology study",
            abstract="An anatomical geometry study describes shape and material distributions.",
            authors=[],
            published_at=date.today(),
            venue="arXiv",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertFalse(passes_candidate_filter(enriched, self.profile))

    def test_generic_reinforcement_learning_cannot_masquerade_as_robotics(self):
        paper = Paper(
            source="crossref",
            source_id="qec",
            title="Reinforcement learning control of quantum error correction",
            abstract="A reinforcement learning controller improves quantum error correction.",
            authors=[],
            published_at=date.today(),
            venue="Nature",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_swarm_language_cannot_admit_geoscience(self):
        paper = Paper(
            source="crossref",
            source_id="seafloor",
            title="Anatomy of a seafloor spreading event captured by in situ seismogeodesy",
            abstract="Seismic swarm observations reveal tectonic change at a mid-ocean ridge.",
            authors=[],
            published_at=date.today(),
            venue="Nature",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_biological_nutrition_cannot_masquerade_as_bioinspired_robotics(self):
        paper = Paper(
            source="crossref",
            source_id="honeybee",
            title="Nutrition of honeybees during learning flights",
            abstract="Honeybees use nutrition to support learning and sensory development.",
            authors=[],
            published_at=date.today(),
            venue="Current Biology",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "venue_not_allowed")

    def test_human_trajectory_prediction_is_not_robot_perception(self):
        paper = Paper(
            source="arxiv",
            source_id="human-trajectory",
            title="EgoTraj: Real-World Egocentric Human Trajectory Dataset",
            abstract="Human trajectory prediction can help mobile robots plan safely in crowds.",
            authors=[],
            published_at=date.today(),
            venue="arXiv",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_survey_is_not_a_weekly_research_recommendation(self):
        paper = Paper(
            source="arxiv",
            source_id="legged-survey",
            title="A Survey of Legged Robotics in Non-Inertial Environments",
            abstract="This survey reviews robot perception, locomotion, and control methods.",
            authors=[],
            published_at=date.today(),
            venue="arXiv",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_overview_and_challenges_article_is_not_a_weekly_research_recommendation(self):
        paper = Paper(
            source="arxiv",
            source_id="sonar-overview",
            title="Sonar-based Deep Learning in Underwater Robotics: Overview, Robustness and Challenges",
            abstract="The overview discusses robot perception and underwater navigation research.",
            authors=[],
            published_at=date.today(),
            venue="arXiv",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_perspective_article_is_not_a_weekly_research_recommendation(self):
        paper = Paper(
            source="arxiv",
            source_id="manipulation-perspective",
            title="A Perspective on Open Challenges in Deformable Object Manipulation",
            abstract="This perspective discusses robotic manipulation, tactile sensing, and control.",
            authors=[],
            published_at=date.today(),
            venue="arXiv",
        )
        enriched = enrich_papers([paper], self.profile)[0]
        audit = candidate_audit(enriched, self.profile)
        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "excluded_domain")

    def test_publisher_metadata_rejects_news_and_views_before_download(self):
        paper = Paper(
            source="crossref",
            source_id="nature-news",
            title="Agentic simulation perfects perception of quadruped robots",
            abstract="A quadruped robot simulation supports SLAM perception.",
            authors=[],
            published_at=date.today(),
            venue="Nature Sensors",
            raw={"paper_radar_article_type": {"accepted": False, "article_type": "news & views"}},
        )

        audit = candidate_audit(enrich_papers([paper], self.profile)[0], self.profile)

        self.assertFalse(audit["accepted"])
        self.assertEqual(audit["reason"], "non_research_article_type")

    def test_transferable_perception_only_still_needs_embodied_platform(self):
        paper = Paper(
            source="crossref",
            source_id="7",
            title="Human navigation under spatial uncertainty",
            abstract="A perception and navigation model for human path planning.",
            authors=[],
            published_at=date.today(),
            venue="Nature Communications",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertFalse(passes_candidate_filter(enriched, self.profile))

    def test_short_acronyms_do_not_match_inside_unrelated_words(self):
        paper = Paper(
            source="crossref",
            source_id="6",
            title="Nanobody conjugates for immunotherapies",
            abstract="A modular biomedical method with ideal controlled release.",
            authors=[],
            published_at=date.today(),
            venue="Nature Electronics",
        )

        enriched = enrich_papers([paper], self.profile)[0]

        self.assertFalse(passes_candidate_filter(enriched, self.profile))


if __name__ == "__main__":
    unittest.main()
