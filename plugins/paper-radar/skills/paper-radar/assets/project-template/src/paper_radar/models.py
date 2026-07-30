from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class Paper:
    source: str
    source_id: str
    title: str
    abstract: str
    authors: List[str]
    published_at: date
    venue: str = ""
    doi: Optional[str] = None
    url: str = ""
    pdf_url: str = ""
    robot_type_tags: List[str] = field(default_factory=list)
    paper_type: str = "transferable"
    signal_groups: List[str] = field(default_factory=list)
    fulltext: str = ""
    key_figure_path: str = ""
    key_figure_caption: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoreBreakdown:
    venue_author_score: float
    relevance_score: float
    evidence_score: float
    freshness_score: float
    diversity_score: float
    total_score: float


@dataclass
class ScoredPaper:
    paper: Paper
    score: ScoreBreakdown


@dataclass
class PaperAnalysis:
    core_insight: str
    problem_frame: str
    first_principles: str
    mechanism: str
    boundary_advanced: str
    old_problem: str
    why_it_works: str
    true_novelty: str
    evidence_summary: str
    email_summary: str
    importance_reason: str
