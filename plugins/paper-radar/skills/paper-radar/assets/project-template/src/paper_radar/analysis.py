from abc import ABC, abstractmethod
import json
import os
from typing import Dict, Iterable
from urllib.request import Request, urlopen

from .models import Paper, PaperAnalysis, ScoredPaper


class AnalysisProvider(ABC):
    @abstractmethod
    def analyze(self, paper: Paper) -> PaperAnalysis:
        raise NotImplementedError


class HeuristicAnalysisProvider(AnalysisProvider):
    """Deterministic fallback used until a production LLM provider is wired in."""

    def analyze(self, paper: Paper) -> PaperAnalysis:
        subject = paper.robot_type_tags[0].replace("_", " ") if paper.robot_type_tags else "transferable perception systems"
        benchmark_hint = "公开 benchmark 上有显著结果" if _mentions_benchmark(paper) else "摘要中尚未看到明确 benchmark 证据"
        real_world_hint = "并给出了真实机器人 / 真实场景验证" if _mentions_real_world(paper) else "但真实部署证据仍需阅读全文确认"
        return PaperAnalysis(
            core_insight=f"当前仅有摘要，尚不足以可靠还原 {subject} 的核心科学洞见。",
            problem_frame=f"该工作围绕 {subject} 的感知任务展开，但仅凭摘要还不能准确确认其真正受限的物理瓶颈。",
            first_principles="需要从原文中确认作者究竟在优化哪一个基本约束：信息获取、噪声、时延、负载、形变，还是可观测性。",
            mechanism="摘要不足以支撑机制级判断；生产版本应读取全文后再给出因果链。",
            boundary_advanced="当前无法负责任地判断它把能力边界推进了多少。",
            old_problem=f"这类工作试图缓解 {subject} 中传统感知模块难安装、难稳定工作的老问题。",
            why_it_works="从摘要看，方法通过更贴合任务约束的传感或表征设计来提升可用性。",
            true_novelty="当前骨架阶段仅基于标题与摘要做初判；真正的新意仍需要接入全文或更强分析器后确认。",
            evidence_summary=f"{benchmark_hint}，{real_world_hint}。",
            email_summary=f"聚焦 {subject} 的感知能力提升；{benchmark_hint}，{real_world_hint}。",
            importance_reason="与研究画像高度相关，且具备继续深读的潜力。",
        )


class OpenAIAnalysisProvider(AnalysisProvider):
    def __init__(self, api_key: str, model: str = "gpt-5-mini") -> None:
        self.api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls):
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return None
        return cls(api_key=api_key, model=os.getenv("OPENAI_MODEL", "gpt-5-mini"))

    def analyze(self, paper: Paper) -> PaperAnalysis:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are a careful research analyst for embodied perception. "
                                "Only use the supplied metadata and abstract. "
                                "If evidence is weak or absent, say so plainly. "
                                "Write in concise Chinese for an expert reader."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _analysis_prompt(paper),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "paper_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "core_insight": {"type": "string"},
                            "problem_frame": {"type": "string"},
                            "first_principles": {"type": "string"},
                            "mechanism": {"type": "string"},
                            "boundary_advanced": {"type": "string"},
                            "old_problem": {"type": "string"},
                            "why_it_works": {"type": "string"},
                            "true_novelty": {"type": "string"},
                            "evidence_summary": {"type": "string"},
                            "email_summary": {"type": "string"},
                            "importance_reason": {"type": "string"},
                        },
                        "required": [
                            "core_insight",
                            "problem_frame",
                            "first_principles",
                            "mechanism",
                            "boundary_advanced",
                            "old_problem",
                            "why_it_works",
                            "true_novelty",
                            "evidence_summary",
                            "email_summary",
                            "importance_reason",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
        }
        req = Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read().decode("utf-8"))
        data = json.loads(_extract_output_text(response))
        return PaperAnalysis(**data)


def analyze_selected(
    scored_papers: Iterable[ScoredPaper],
    provider: AnalysisProvider,
    paper_id_fn,
) -> Dict[str, PaperAnalysis]:
    return {
        paper_id_fn(item.paper): provider.analyze(item.paper)
        for item in scored_papers
    }


def _mentions_benchmark(paper: Paper) -> bool:
    haystack = f"{paper.title} {paper.abstract}".lower()
    return any(token in haystack for token in ["benchmark", "state-of-the-art", "sota", "outperform"])


def _mentions_real_world(paper: Paper) -> bool:
    haystack = f"{paper.title} {paper.abstract}".lower()
    return any(token in haystack for token in ["real robot", "real-world", "real world", "hardware experiment"])


def choose_analysis_provider() -> AnalysisProvider:
    return OpenAIAnalysisProvider.from_env() or HeuristicAnalysisProvider()


def _analysis_prompt(paper: Paper) -> str:
    source_text = paper.fulltext.strip() or paper.abstract
    source_label = "全文" if paper.fulltext.strip() else "摘要"
    return f"""
论文标题：{paper.title}
作者：{", ".join(paper.authors)}
Venue：{paper.venue}
机器人类型标签：{", ".join(paper.robot_type_tags) or "none"}
论文类型：{paper.paper_type}
可用材料类型：{source_label}
材料：
{source_text[:50000]}

请像资深 PI 给组会做判断一样，按第一性原理理解这篇论文。

要求：
- 不要复述摘要。
- 不要写空话，例如“提出了一个更高效的方法”“提升了感知能力”。
- 先找出系统真正受限的基本约束，再说明作者利用了哪个杠杆打破它。
- 如果摘要不足以支持某个结论，必须直接说“不足以判断”。
- 如果提供的是全文，请优先依据全文中的方法、实验、消融、讨论部分作答。
- 语言要让研究者在 30 秒内 get 到论文，而不是得到一段宣传文案。

请输出：
1. core_insight：一句话说清真正的科学洞见
2. problem_frame：把问题重新表述成一个基本约束 / trade-off
3. first_principles：从第一性原理看，作者利用了什么
4. mechanism：方法为什么会生效，给出因果链
5. boundary_advanced：它把能力边界往前推了多少
6. old_problem：它解决了什么老问题
7. why_it_works：方法为何成立
8. true_novelty：相比已有工作真正新在哪
9. evidence_summary：benchmark / 真实场景证据摘要
10. email_summary：适合邮件的短段落摘要
11. importance_reason：为什么值得该研究画像关注
""".strip()


def _extract_output_text(response: Dict) -> str:
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise ValueError("No output_text found in OpenAI response.")
