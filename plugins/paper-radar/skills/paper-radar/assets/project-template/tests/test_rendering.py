import re
import unittest
from datetime import date, timedelta

from paper_radar.models import Paper, PaperAnalysis, ScoreBreakdown, ScoredPaper
from paper_radar.rendering import quick_take, render_digest_page


def scored(source, source_id, title, venue, published_at, score=0.6, tags=None):
    return ScoredPaper(
        paper=Paper(
            source=source,
            source_id=source_id,
            title=title,
            abstract=title,
            authors=[],
            published_at=published_at,
            venue=venue,
            robot_type_tags=tags or ["other"],
            paper_type="direct" if source != "arxiv" else "transferable",
            url=f"https://example.test/{source_id}",
            pdf_url=f"https://example.test/{source_id}.pdf",
            key_figure_path=f"output/figures/{source_id}.png",
        ),
        score=ScoreBreakdown(score, score, score, score, score, score),
    )


def analysis(
    core_insight="这是一句核心洞见，不应该作为卡片速读的首选。",
    problem_frame="移动机器人在任意方向运动时动力能力不均匀，导致控制器必须补偿方向偏置。",
    mechanism="提出 dynamic isotropy 指标并把可伸缩腿均匀分布在球形机体上生成近似全向推力。",
    true_novelty="可量化的动力学各向同性设计原则。",
    importance_reason="它说明感知只有落到动作约束里才真正改变机器人能力。",
):
    return PaperAnalysis(
        core_insight=core_insight,
        problem_frame=problem_frame,
        first_principles="加速度能力来自质量分布、接触点和执行器约束。",
        mechanism=mechanism,
        boundary_advanced="把形态设计推进到动力学能力设计。",
        old_problem="过去全向运动依赖复杂控制补偿。",
        why_it_works="均匀接触点让每个方向都有相近的力生成组合。",
        true_novelty=true_novelty,
        evidence_summary="Science Robotics 正式论文；摘要报告超过 1000 种模拟形态和 20-leg 硬件实验提升 tracking、robustness 和 energy efficiency。",
        email_summary="邮件短摘要。",
        importance_reason=importance_reason,
    )


def masthead_lede(page_html):
    match = re.search(r'<div class="lede">\s*(.*?)\s*</div>', page_html, re.S)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


class RenderingTests(unittest.TestCase):
    def test_digest_display_order_follows_tier_rule(self):
        today = date(2026, 6, 1)
        items = [
            scored("arxiv", "preprint", "arXiv tactile robot learning", "arXiv", today, 0.99),
            scored("openalex", "tier2", "Nature Communications tactile sensing", "Nature Communications", today, 0.8),
            scored("crossref", "tier1-old", "Science Robotics wheeled-legged navigation", "Science Robotics", today - timedelta(days=30), 0.9),
            scored("crossref", "tier1-new", "Extreme dynamic symmetry enables omnidirectional robots", "Science Robotics", today, 0.5),
        ]
        analyses = {item.paper.source_id: analysis() for item in items}

        html = render_digest_page(items, analyses, lambda paper: paper.source_id, today)

        self.assertLess(
            html.index("Extreme dynamic symmetry enables omnidirectional robots"),
            html.index("Science Robotics wheeled-legged navigation"),
        )
        self.assertLess(
            html.index("Science Robotics wheeled-legged navigation"),
            html.index("Nature Communications tactile sensing"),
        )
        self.assertLess(
            html.index("Nature Communications tactile sensing"),
            html.index("arXiv tactile robot learning"),
        )
        self.assertIn("Tier 1 旗舰正式论文", html)
        self.assertIn("arXiv / preprint", html)

    def test_digest_copy_is_dynamic_and_not_stale(self):
        today = date(2026, 6, 1)
        items = [
            scored("crossref", "argus", "Extreme dynamic symmetry enables omnidirectional and multifunctional robots", "Science Robotics", today),
            scored("openalex", "tactile", "Multimodal tactile sensing fused with vision for dexterous robotic housekeeping", "Nature Communications", today),
            scored("arxiv", "rdgen", "RDGen demonstration generation for robot learning", "arXiv", today),
        ]
        analyses = {item.paper.source_id: analysis() for item in items}

        html = render_digest_page(items, analyses, lambda paper: paper.source_id, today)
        lede = masthead_lede(html)

        self.assertIn("<h1>具身智能/感知精选</h1>", html)
        self.assertNotIn("面向微型、仿生、软体与其他传统感知模块不易安装的平台", html)
        self.assertNotIn("10 篇论文 · 8 篇直接命中特殊机器人感知 · 2 篇可迁移前沿方法", html)
        self.assertNotIn("本期按 Tier 1", html)
        self.assertNotIn("开篇包括", html)
        self.assertIn("1 篇旗舰正式论文", html)
        self.assertIn("1 篇高质量正式论文", html)
        self.assertIn("1 篇 arXiv/preprint", html)
        self.assertLessEqual(len(lede), 64)
        self.assertIn("本期围绕", lede)
        self.assertIn("核心问题是让感知", lede)
        self.assertIn("转化为可执行的动作约束", lede)
        self.assertNotIn("给出可检验路径", lede)
        self.assertIn("先看过去全向运动依赖复杂控制补偿如何限制真实任务", html)
        self.assertIn("如何由Science Robotics 正式论文支撑", html)
        self.assertNotIn("论文把突破口放在", html)
        self.assertNotIn("原则...", html)

    def test_editorial_copy_changes_with_current_issue_analysis(self):
        today = date(2026, 6, 1)
        items = [
            scored("crossref", "same-theme", "Science Robotics tactile robot manipulation", "Science Robotics", today),
            scored("arxiv", "support", "arXiv tactile robot learning", "arXiv", today),
        ]
        analyses_a = {
            "same-theme": analysis(
                core_insight="触觉反馈把灵巧操作从视觉猜测推进到接触后的连续修正。",
                problem_frame="灵巧夹爪在滑移出现后才真正暴露控制误差。",
                mechanism="用高频触觉信号闭环修正抓取力和手指轨迹。",
                importance_reason="它说明接触后的信息比接触前的识别更能决定操作成败。",
            ),
            "support": analysis(),
        }
        analyses_b = {
            "same-theme": analysis(
                core_insight="软体传感皮肤把材料形变变成机器人自身状态的读数。",
                problem_frame="软体机器人缺少稳定的内部形态测量。",
                mechanism="用分布式应变场重建连续体弯曲和外部载荷。",
                importance_reason="它把感知从外置传感器转移到身体材料内部。",
            ),
            "support": analysis(),
        }

        html_a = render_digest_page(items, analyses_a, lambda paper: paper.source_id, today)
        html_b = render_digest_page(items, analyses_b, lambda paper: paper.source_id, today)
        lede_a = masthead_lede(html_a)
        lede_b = masthead_lede(html_b)

        self.assertIn("支撑接触后的闭环修正", lede_a)
        self.assertIn("成为身体内部的状态读数", lede_b)
        self.assertLessEqual(len(lede_a), 64)
        self.assertLessEqual(len(lede_b), 64)
        self.assertNotIn("高频触觉信号闭环修正抓取力和手指轨迹", lede_a)
        self.assertNotIn("分布式应变场重建连续体弯曲和外部载荷", lede_b)
        self.assertNotEqual(html_a, html_b)
        self.assertNotIn("真正的操作，发生在接触之后", html_a)
        self.assertNotIn("真正的操作，发生在接触之后", html_b)

    def test_issue_editorial_override_supplies_calvino_style_guide(self):
        today = date(2026, 6, 1)
        items = [
            scored("crossref", "same-theme", "Science Robotics tactile robot manipulation", "Science Robotics", today),
            scored("arxiv", "support", "arXiv tactile robot learning", "arXiv", today),
        ]
        analyses = {item.paper.source_id: analysis() for item in items}
        issue_editorial = {
            "lede": "这些论文把机器人带到接触的门口，真正迷人的地方是误差开始说话。",
            "panel_quote": "导读：不要把它们看成一组算法竞赛；更有意思的是，机器在触碰、迟疑和纠错时，才第一次显出自己的感知。",
        }

        html = render_digest_page(items, analyses, lambda paper: paper.source_id, today, issue_editorial=issue_editorial)

        self.assertIn(issue_editorial["lede"], masthead_lede(html))
        self.assertIn(issue_editorial["panel_quote"], html)
        self.assertLessEqual(len(masthead_lede(html)), 72)

    def test_issue_editorial_override_rejects_overlong_or_truncated_copy(self):
        today = date(2026, 6, 1)
        items = [
            scored("crossref", "same-theme", "Science Robotics tactile robot manipulation", "Science Robotics", today),
            scored("arxiv", "support", "arXiv tactile robot learning", "arXiv", today),
        ]
        analyses = {item.paper.source_id: analysis() for item in items}
        issue_editorial = {
            "lede": "这是一段过长的导读，它把问题、方法、证据、影响、历史脉络、未来方向、技术细节、领域争论、实验边界、工程启示和所有漂亮形容词都塞进一个本该轻盈的门口句子里。",
            "panel_quote": "导读：这句话故意留下省略号...",
        }

        html = render_digest_page(items, analyses, lambda paper: paper.source_id, today, issue_editorial=issue_editorial)

        self.assertNotIn(issue_editorial["lede"], html)
        self.assertNotIn("省略号", html)
        self.assertIn("本期围绕", masthead_lede(html))

    def test_quick_take_uses_problem_method_and_evidence(self):
        text = quick_take(analysis())

        self.assertIn("针对移动机器人", text)
        self.assertIn("论文通过提出 dynamic isotropy 指标", text)
        self.assertIn("20-leg 硬件实验", text)
        self.assertNotIn("这是一句核心洞见", text)
        self.assertNotIn("...", text)


if __name__ == "__main__":
    unittest.main()
