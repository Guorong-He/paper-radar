from datetime import date
import re
from typing import Any, Dict, Iterable, List

from .models import Paper, ScoreBreakdown, ScoredPaper
from .venues import matches_preferred_venue


def score_papers(papers: Iterable[Paper], profile: Dict[str, Any], today: date) -> List[ScoredPaper]:
    scored = []
    for paper in papers:
        venue_author = venue_author_score(paper, profile)
        relevance = relevance_score(paper, profile)
        evidence = evidence_score(paper, profile)
        freshness = freshness_score(paper, today, profile)
        diversity = 0.5 if paper.robot_type_tags else 0.25
        priority = user_priority_score(paper, profile)
        weights = profile["weights"]
        total = (
            venue_author * weights["venue_author_score"]
            + relevance * weights["relevance_score"]
            + evidence * weights["evidence_score"]
            + freshness * weights["freshness_score"]
            + diversity * weights["diversity_score"]
            + priority * weights.get("user_priority_score", 0.0)
        )
        scored.append(
            ScoredPaper(
                paper=paper,
                score=ScoreBreakdown(
                    venue_author_score=venue_author,
                    relevance_score=relevance,
                    evidence_score=evidence,
                    freshness_score=freshness,
                    diversity_score=diversity,
                    total_score=round(min(1.0, total), 4),
                ),
            )
        )
    return scored


def venue_author_score(paper: Paper, profile: Dict[str, Any]) -> float:
    venue_hit = matches_preferred_venue(paper.venue or "", profile["preferred_venues"])
    author_blob = " ".join(paper.authors).lower()
    author_hit = any(author.lower() in author_blob for author in profile["preferred_authors"])
    lab_hit = any(lab.lower() in f"{paper.title} {paper.abstract}".lower() for lab in profile["preferred_labs"])
    if venue_hit and (author_hit or lab_hit):
        return 1.0
    if venue_hit:
        return 0.8
    if author_hit or lab_hit:
        return 0.7
    return 0.2


def relevance_score(paper: Paper, profile: Dict[str, Any]) -> float:
    haystack = f"{paper.title} {paper.abstract}".lower()
    hits = sum(1 for keyword in profile["relevance_keywords"] if keyword.lower() in haystack)
    return min(1.0, hits / 4.0)


def evidence_score(paper: Paper, profile: Dict[str, Any]) -> float:
    haystack = f"{paper.title} {paper.abstract}".lower()
    groups = profile["evidence_keywords"]
    benchmark_hit = any(k.lower() in haystack for k in groups["benchmark"])
    real_world_hit = any(k.lower() in haystack for k in groups["real_world"])
    if benchmark_hit and real_world_hit:
        return 1.0
    if benchmark_hit or real_world_hit:
        return 0.6
    return 0.2


def user_priority_score(paper: Paper, profile: Dict[str, Any]) -> float:
    keywords = profile.get("user_priority_keywords", [])
    if not keywords:
        return 0.0
    haystack = f"{paper.title} {paper.abstract}".lower()
    hits = sum(1 for keyword in keywords if _keyword_in_text(keyword, haystack))
    if not hits:
        return 0.0

    keyword_score = min(1.0, hits / 2.0)
    if matches_preferred_venue(paper.venue or "", profile.get("tier1_broad_venues", [])):
        return keyword_score
    if paper.source != "arxiv" and matches_preferred_venue(paper.venue or "", profile.get("preferred_venues", [])):
        return keyword_score * 0.5
    return keyword_score * 0.25


def freshness_score(paper: Paper, today: date, profile: Dict[str, Any]) -> float:
    age_days = max(0, (today - paper.published_at).days)
    fresh_days = profile["selection"]["fresh_days"]
    lookback_days = profile["selection"]["lookback_days"]
    if age_days <= fresh_days:
        return 1.0
    if age_days >= lookback_days:
        return 0.2
    span = lookback_days - fresh_days
    return round(1.0 - 0.8 * ((age_days - fresh_days) / span), 4)


def _keyword_in_text(keyword: str, haystack: str) -> bool:
    needle = " ".join((keyword or "").lower().split())
    text = " ".join((haystack or "").lower().split())
    if not needle:
        return False
    if len(needle) <= 3 or " " not in needle:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text
