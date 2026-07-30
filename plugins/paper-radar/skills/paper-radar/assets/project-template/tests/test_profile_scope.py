import unittest
from datetime import date

from paper_radar.config import load_profile
from paper_radar.models import Paper
from paper_radar.scoring import score_papers, user_priority_score
from paper_radar.tagging import enrich_papers, passes_candidate_filter


class ProfileScopeTests(unittest.TestCase):
    def test_profile_covers_nsc_subjournals_and_mobile_perception_venues(self):
        profile = load_profile()
        watched = {venue.lower() for venue in profile["venue_watchlist"]}
        preferred = {venue.lower() for venue in profile["preferred_venues"]}
        queries = " ".join(profile["authority_queries"]).lower()

        expected_venues = [
            "nature machine intelligence",
            "science robotics",
            "matter",
            "mobicom",
            "mobisys",
            "sensys",
            "ipsn",
            "icra",
            "iros",
        ]
        for venue in expected_venues:
            self.assertIn(venue, watched)
            self.assertIn(venue, preferred)
        self.assertIn("proceedings of the acm on interactive, mobile, wearable and ubiquitous technologies", preferred)
        self.assertNotIn("proceedings of the acm on interactive, mobile, wearable and ubiquitous technologies", watched)

        excluded_low_floor = [
            "scientific reports",
            "npj robotics",
            "cell reports physical science",
            "patterns",
            "iscience",
        ]
        for venue in excluded_low_floor:
            self.assertNotIn(venue, watched)
            self.assertNotIn(venue, preferred)

        for query in [
            "mobile sensing",
            "wearable sensing",
            "ubiquitous sensing",
            "sensor fusion",
            "underwater robot sensing",
            "dielectric elastomer actuator sensing",
            "humanoid robot tactile perception",
            "wireless sensing robot perception",
            "robot manipulation",
            "robot locomotion",
            "dynamic symmetry robot",
            "omnidirectional robot",
            "robot morphology",
            "robot system design",
            "bioinspired robot navigation",
            "insect-inspired robot navigation",
            "honeybee robot navigation",
            "visual homing robot navigation",
            "learning flights robot",
            "mobile robot navigation",
            "robot motion planning",
            "sim-to-real robotics",
            "human robot interaction",
        ]:
            self.assertIn(query, queries)

        focus = " ".join(profile["research_focus"]).lower()
        relevance = " ".join(profile["relevance_keywords"]).lower()
        self.assertIn("robotics", focus)
        self.assertIn("dexterous manipulation", relevance)
        self.assertIn("swarm robotics", relevance)
        self.assertIn("robotics", profile["required_signal_groups"])
        self.assertIn("Science Robotics", profile["tier1_broad_venues"])
        self.assertIn("Nature Electronics", profile["tier1_broad_venues"])
        self.assertIn("Nature Machine Intelligence", profile["tier1_broad_venues"])
        self.assertNotIn("Nature Communications", profile["tier1_broad_venues"])
        self.assertNotIn("Science Advances", profile["tier1_broad_venues"])
        self.assertIn("space robot", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("underground robot", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("planetary rover", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("bioinspired electronics", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("electronic sensing system", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("large language model", " ".join(profile["broad_robotics_keywords"]).lower())
        self.assertIn("vision-language-action", " ".join(profile["broad_robotics_keywords"]).lower())
        priority = " ".join(profile["user_priority_keywords"]).lower()
        self.assertIn("dynamic symmetry", priority)
        self.assertIn("omnidirectional robot", priority)
        self.assertIn("robot morphology", priority)
        self.assertIn("robot system design", priority)
        self.assertIn("visual homing", priority)
        self.assertIn("honeybee learning flights", priority)
        self.assertGreater(profile["weights"]["user_priority_score"], 0)

        self.assertGreaterEqual(profile["selection"]["figure_candidate_pool"], 120)
        self.assertGreaterEqual(profile["selection"]["official_figure_candidate_pool"], 180)
        self.assertLessEqual(profile["selection"]["preprint_figure_candidate_pool"], 30)
        self.assertEqual(profile["selection"]["tier1_target"], 3)
        self.assertEqual(profile["selection"]["tier2_min"], 4)
        self.assertEqual(profile["selection"]["tier2_max"], 5)
        self.assertEqual(profile["selection"]["preprint_min"], 2)
        self.assertEqual(profile["selection"]["preprint_max"], 3)
        self.assertTrue(profile["selection"]["exclude_previous_recommendations"])
        self.assertGreaterEqual(profile["selection"]["official_min"], 7)
        self.assertEqual(profile["selection"]["lookback_days"], 1095)
        self.assertEqual(profile["selection"]["backfill_days"], 1095)
        self.assertEqual(profile["selection"]["official_backfill_days"], 1095)
        self.assertNotIn("device", watched)
        self.assertNotIn("device", preferred)
        self.assertIn("10.1038/s41586-026-10461-3", profile["must_watch_dois"])
        publisher_feeds = {feed["name"].lower(): feed["url"] for feed in profile["publisher_toc_feeds"]}
        self.assertIn("nature", publisher_feeds)
        self.assertIn("nature machine intelligence", publisher_feeds)
        self.assertIn("nature communications", publisher_feeds)
        self.assertIn("nature sensors", publisher_feeds)
        self.assertTrue(publisher_feeds["nature"].endswith("/nature/research-articles.rss"))
        publisher_signals = " ".join(profile["publisher_toc_signals"]).lower()
        self.assertIn("honeybee", publisher_signals)
        self.assertIn("visual homing", publisher_signals)
        self.assertIn("learning flights", publisher_signals)
        self.assertGreaterEqual(profile["selection"]["max_publisher_toc_items_per_feed"], 30)
        self.assertLessEqual(profile["selection"]["publisher_toc_timeout_seconds"], 12)

    def test_honeybee_learning_flights_nature_robot_navigation_is_in_scope(self):
        profile = load_profile()
        paper = Paper(
            source="crossref",
            source_id="10.1038/s41586-026-10461-3",
            title="Efficient robot navigation inspired by honeybee learning flights",
            abstract=(
                "Navigation is a crucial capability for both animals and robots. "
                "Bee-Nav is a highly efficient navigation strategy inspired by visual learning flights "
                "that maps omnidirectional images to a home vector using path integration."
            ),
            authors=["Dequan Ou", "Guido C. H. E. de Croon"],
            published_at=date(2026, 5, 13),
            venue="Nature",
            doi="10.1038/s41586-026-10461-3",
        )

        enriched = enrich_papers([paper], profile)[0]
        scored = score_papers([enriched], profile, date(2026, 6, 8))[0]

        self.assertTrue(passes_candidate_filter(enriched, profile))
        # Biological inspiration alone is no longer a direct-admission signal;
        # the explicit robot/navigation wording is what keeps this paper in scope.
        self.assertNotIn("bioinspired", enriched.robot_type_tags)
        self.assertIn("mobile_robot", enriched.robot_type_tags)
        self.assertGreaterEqual(user_priority_score(enriched, profile), 0.5)
        self.assertGreaterEqual(scored.score.total_score, 0.65)

    def test_tier1_bioinspired_electronic_sensing_is_in_scope(self):
        profile = load_profile()
        paper = Paper(
            source="crossref",
            source_id="10.1038/tier1-bioelectronics",
            title="Bioinspired electronic skin for distributed tactile sensing",
            abstract="A low-power bioelectronic sensor system integrates adaptive electronic sensing arrays.",
            authors=[],
            published_at=date.today(),
            venue="Nature Electronics",
            doi="10.1038/tier1-bioelectronics",
        )

        enriched = enrich_papers([paper], profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, profile))

    def test_tier1_embodied_foundation_model_system_is_in_scope(self):
        profile = load_profile()
        paper = Paper(
            source="crossref",
            source_id="10.1038/tier1-foundation-model",
            title="A foundation model system for embodied intelligence",
            abstract="The multimodal foundation model couples a world model with vision-language-action policies.",
            authors=[],
            published_at=date.today(),
            venue="Nature Machine Intelligence",
            doi="10.1038/tier1-foundation-model",
        )

        enriched = enrich_papers([paper], profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, profile))

    def test_millimeter_scale_biology_does_not_become_micro_robot_scope(self):
        profile = load_profile()
        paper = Paper(
            source="crossref",
            source_id="10.1038/s41592-026-03066-1",
            title="A multimodal adaptive optical microscope for in vivo imaging from molecules to organisms",
            abstract=(
                "MOSAIC enables noninvasive imaging of subcellular dynamics in cultured cells "
                "and live multicellular organisms, nanoscale mapping of molecular architectures "
                "across millimeter-scale expanded tissues and neural imaging within live mice."
            ),
            authors=[],
            published_at=date(2026, 6, 1),
            venue="Nature Methods",
            doi="10.1038/s41592-026-03066-1",
        )

        enriched = enrich_papers([paper], profile)[0]

        self.assertNotIn("micro_robot", enriched.robot_type_tags)
        self.assertFalse(passes_candidate_filter(enriched, profile))

    def test_tactile_wearable_hmi_with_robot_teleoperation_is_in_scope(self):
        profile = load_profile()
        paper = Paper(
            source="openalex",
            source_id="W4391313209",
            title="Adaptive tactile interaction transfer via digitally embroidered smart gloves",
            abstract=(
                "A textile-based wearable human-machine interface embeds tactile sensors and "
                "vibrotactile haptic actuators into smart gloves, records tactile interactions, "
                "and enables responsive robot teleoperation."
            ),
            authors=[],
            published_at=date(2024, 2, 1),
            venue="Nature Communications",
            doi="10.1038/s41467-024-45059-8",
        )

        enriched = enrich_papers([paper], profile)[0]

        self.assertTrue(passes_candidate_filter(enriched, profile))

    def test_human_navigation_without_embodied_platform_is_out_of_scope(self):
        profile = load_profile()
        paper = Paper(
            source="openalex",
            source_id="W4400383841",
            title="Human navigation strategies and their errors result from dynamic interactions of spatial uncertainties",
            abstract=(
                "Goal-directed navigation integrates uncertain self-motion and landmark cues "
                "into path planning and perception for human navigation behavior."
            ),
            authors=[],
            published_at=date(2024, 8, 1),
            venue="Nature Communications",
            doi="10.1038/s41467-024-49722-y",
        )

        enriched = enrich_papers([paper], profile)[0]

        self.assertFalse(passes_candidate_filter(enriched, profile))


if __name__ == "__main__":
    unittest.main()
