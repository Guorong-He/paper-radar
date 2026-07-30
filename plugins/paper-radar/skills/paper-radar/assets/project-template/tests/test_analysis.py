import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from paper_radar.analysis import HeuristicAnalysisProvider
from paper_radar.models import Paper
from paper_radar.packet import load_analyses, load_issue_editorial


class AnalysisTests(unittest.TestCase):
    def test_analysis_contains_required_fields(self):
        paper = Paper(
            source="fixture",
            source_id="1",
            title="Soft Robot Perception",
            abstract="A benchmark study with real robot validation.",
            authors=[],
            published_at=date.today(),
            robot_type_tags=["soft_robot"],
        )
        analysis = HeuristicAnalysisProvider().analyze(paper)
        self.assertTrue(analysis.problem_frame)
        self.assertTrue(analysis.first_principles)
        self.assertTrue(analysis.mechanism)
        self.assertTrue(analysis.email_summary)
        self.assertTrue(analysis.true_novelty)

    def test_load_analyses_allows_issue_editorial_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "analyses.json"
            path.write_text(
                json.dumps(
                    {
                        "_issue_editorial": {
                            "lede": "这些论文把机器人带到接触的门口。",
                            "panel_quote": "导读：真正迷人的地方在这里。",
                        },
                        "fixture:1": {
                            "core_insight": "核心洞见。",
                            "problem_frame": "问题。",
                            "first_principles": "原理。",
                            "mechanism": "机制。",
                            "boundary_advanced": "边界。",
                            "old_problem": "老问题。",
                            "why_it_works": "原因。",
                            "true_novelty": "新意。",
                            "evidence_summary": "证据。",
                            "email_summary": "邮件摘要。",
                            "importance_reason": "重要性。",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            analyses = load_analyses(str(path))
            issue_editorial = load_issue_editorial(str(path))

        self.assertEqual(["fixture:1"], list(analyses))
        self.assertEqual("这些论文把机器人带到接触的门口。", issue_editorial["lede"])


if __name__ == "__main__":
    unittest.main()
