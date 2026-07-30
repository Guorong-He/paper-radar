import unittest
from datetime import date, timedelta

from paper_radar.models import Paper, ScoreBreakdown, ScoredPaper
from paper_radar.selection import select_digest


def scored(source, source_id, paper_type, score):
    return ScoredPaper(
        paper=Paper(source, source_id, source_id, "x", [], date.today(), paper_type=paper_type),
        score=ScoreBreakdown(score, score, score, score, score, score),
    )


def scored_with_venue(source, source_id, venue, published_at, score=1.0):
    return ScoredPaper(
        paper=Paper(
            source,
            source_id,
            source_id,
            "x",
            [],
            published_at,
            venue=venue,
            paper_type="direct",
        ),
        score=ScoreBreakdown(score, score, score, score, score, score),
    )


class SelectionTests(unittest.TestCase):
    def test_validated_local_download_is_prioritized_within_its_tier(self):
        today = date.today()
        ordinary = scored_with_venue("crossref", "ordinary", "Science Robotics", today, score=1.0)
        downloaded = scored_with_venue("crossref", "downloaded", "Science Robotics", today, score=0.5)
        downloaded.paper.raw["paper_radar_local_download"] = {"priority": True}

        selected = select_digest(
            [ordinary, downloaded],
            target_count=1,
            direct_min=0,
            direct_max=1,
            official_min=1,
            preprint_max=0,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=1,
            tier2_min=0,
            tier2_max=0,
            preprint_min=0,
        )

        self.assertEqual(selected[0].paper.source_id, "downloaded")
    def test_official_minimum_is_respected(self):
        items = [
            scored("crossref", "official-1", "transferable", 0.7),
            scored("crossref", "official-2", "direct", 0.65),
            scored("arxiv", "direct-1", "direct", 0.9),
            scored("arxiv", "direct-2", "direct", 0.8),
            scored("arxiv", "transfer-1", "transferable", 0.75),
        ]
        selected = select_digest(items, target_count=4, direct_min=2, direct_max=3, official_min=2)
        self.assertGreaterEqual(sum(1 for item in selected if item.paper.source == "crossref"), 2)

    def test_preprint_max_is_respected(self):
        items = [
            scored("arxiv", f"preprint-{idx}", "transferable", 1.0 - idx * 0.01)
            for idx in range(6)
        ] + [
            scored("crossref", f"official-{idx}", "transferable", 0.7 - idx * 0.01)
            for idx in range(6)
        ]

        selected = select_digest(
            items,
            target_count=6,
            direct_min=0,
            direct_max=3,
            official_min=3,
            preprint_max=2,
        )

        self.assertLessEqual(sum(1 for item in selected if item.paper.source == "arxiv"), 2)
        self.assertEqual(len(selected), 6)

    def test_openalex_counts_as_official_source(self):
        items = [
            scored("openalex", "official-1", "transferable", 0.9),
            scored("openalex", "official-2", "transferable", 0.8),
            scored("arxiv", "preprint-1", "transferable", 0.7),
        ]

        selected = select_digest(
            items,
            target_count=3,
            direct_min=0,
            direct_max=3,
            official_min=2,
            preprint_max=1,
        )

        self.assertEqual([item.paper.source for item in selected[:2]], ["openalex", "openalex"])

    def test_tiered_digest_respects_requested_mix(self):
        today = date.today()
        items = []
        for idx in range(5):
            items.append(scored_with_venue("crossref", f"tier1-{idx}", "Science Robotics", today - timedelta(days=idx)))
        for idx in range(7):
            items.append(scored_with_venue("crossref", f"tier2-{idx}", "Nature Communications", today - timedelta(days=idx)))
        for idx in range(5):
            items.append(scored_with_venue("arxiv", f"preprint-{idx}", "arXiv", today - timedelta(days=idx)))

        selected = select_digest(
            items,
            target_count=10,
            direct_min=0,
            direct_max=10,
            official_min=7,
            preprint_max=3,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=3,
            tier2_min=4,
            tier2_max=5,
            preprint_min=2,
        )

        self.assertEqual(sum(1 for item in selected if item.paper.venue == "Science Robotics"), 3)
        self.assertEqual(sum(1 for item in selected if item.paper.venue == "Nature Communications"), 5)
        self.assertEqual(sum(1 for item in selected if item.paper.source == "arxiv"), 2)

    def test_tiered_digest_rejects_an_issue_when_tier1_target_cannot_be_met(self):
        today = date.today()
        items = [
            scored_with_venue("crossref", "tier1-only", "Science Robotics", today),
            scored_with_venue("crossref", "tier2-a", "Nature Communications", today),
            scored_with_venue("crossref", "tier2-b", "Nature Communications", today),
            scored_with_venue("arxiv", "preprint-a", "arXiv", today),
            scored_with_venue("arxiv", "preprint-b", "arXiv", today),
        ]

        selected = select_digest(
            items,
            target_count=5,
            direct_min=0,
            direct_max=5,
            official_min=3,
            preprint_max=2,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=2,
            tier2_min=1,
            tier2_max=2,
            preprint_min=1,
        )

        self.assertEqual(selected, [])

    def test_tiered_digest_prefers_relevance_score_before_freshness(self):
        today = date.today()
        items = [
            scored_with_venue("crossref", "newer", "Science Robotics", today, score=0.5),
            scored_with_venue("crossref", "older", "Science Robotics", today - timedelta(days=100), score=1.0),
        ]

        selected = select_digest(
            items,
            target_count=1,
            direct_min=0,
            direct_max=1,
            official_min=1,
            preprint_max=0,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=1,
            tier2_min=0,
            tier2_max=0,
            preprint_min=0,
        )

        self.assertEqual(selected[0].paper.source_id, "older")

    def test_tiered_digest_excludes_non_whitelisted_formal_venues(self):
        today = date.today()
        items = [
            scored_with_venue("crossref", "tier1", "Science Robotics", today, score=0.8),
            scored_with_venue("crossref", "tier2", "Nature Communications", today, score=0.7),
            scored_with_venue("crossref", "other", "Lab on a Chip", today, score=1.0),
            scored_with_venue("arxiv", "preprint", "arXiv", today, score=0.6),
        ]

        selected = select_digest(
            items,
            target_count=4,
            direct_min=0,
            direct_max=4,
            official_min=0,
            preprint_max=1,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=1,
            tier2_min=1,
            tier2_max=1,
            preprint_min=1,
        )

        self.assertEqual({item.paper.source_id for item in selected}, {"tier1", "tier2", "preprint"})

    def test_user_priority_score_does_not_override_three_tier_mix(self):
        today = date.today()
        items = []
        for idx in range(5):
            items.append(
                scored_with_venue(
                    "crossref",
                    f"tier1-{idx}",
                    "Science Robotics",
                    today - timedelta(days=idx),
                    score=0.6 - idx * 0.01,
                )
            )
        for idx in range(7):
            items.append(
                scored_with_venue(
                    "crossref",
                    f"tier2-{idx}",
                    "Nature Communications",
                    today - timedelta(days=idx),
                    score=0.55 - idx * 0.01,
                )
            )
        for idx in range(6):
            items.append(
                scored_with_venue(
                    "arxiv",
                    f"preprint-priority-{idx}",
                    "arXiv",
                    today - timedelta(days=idx),
                    score=1.0 - idx * 0.01,
                )
            )

        selected = select_digest(
            items,
            target_count=10,
            direct_min=0,
            direct_max=10,
            official_min=7,
            preprint_max=3,
            tier1_broad_venues=["Science Robotics"],
            tier2_venues=["Nature Communications"],
            tier1_target=3,
            tier2_min=4,
            tier2_max=5,
            preprint_min=2,
        )

        self.assertEqual(sum(1 for item in selected if item.paper.venue == "Science Robotics"), 3)
        self.assertEqual(sum(1 for item in selected if item.paper.venue == "Nature Communications"), 5)
        self.assertEqual(sum(1 for item in selected if item.paper.source == "arxiv"), 2)


if __name__ == "__main__":
    unittest.main()
