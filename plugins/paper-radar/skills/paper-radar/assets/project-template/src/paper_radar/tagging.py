import re
from typing import Any, Dict, Iterable, List

from .models import Paper
from .venues import matches_preferred_venue


def enrich_papers(papers: Iterable[Paper], profile: Dict[str, Any]) -> List[Paper]:
    enriched = []
    for paper in papers:
        haystack = f"{paper.title} {paper.abstract}".lower()
        paper.robot_type_tags = [
            tag
            for tag, keywords in profile["robot_type_keywords"].items()
            if any(_keyword_in_text(keyword, haystack) for keyword in keywords)
        ]
        paper.signal_groups = [
            group_name
            for group_name, keywords in profile["required_signal_groups"].items()
            if any(_keyword_in_text(keyword, haystack) for keyword in keywords)
        ]
        paper.paper_type = classify_paper_type(paper, profile, haystack)
        enriched.append(paper)
    return enriched


def classify_paper_type(paper: Paper, profile: Dict[str, Any], haystack: str = "") -> str:
    """Classify only explicit embodied-system work as direct.

    Topic words such as "swarm", "reinforcement learning", and "bioinspired"
    describe methods or biological inspiration, not an embodied platform.  They
    must never convert a non-robotics paper into a direct recommendation.
    """

    text = haystack or f"{paper.title} {paper.abstract}".lower()
    if _strict_platform_evidence(profile, text) and _embodied_task_evidence(profile, text):
        return "direct"
    return "transferable"


def passes_candidate_filter(paper: Paper, profile: Dict[str, Any]) -> bool:
    return candidate_audit(paper, profile)["accepted"]


def candidate_audit(paper: Paper, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Return an inspectable, metadata-only semantic admission record.

    The audit is deliberately independent of venue prestige, score, figure, and
    recency.  It exposes the two pieces of evidence that must exist before a
    paper can enter the Paper Radar pool: an embodied platform and an embodied
    task.  This prevents accidental admission of quantum, geoscience, or pure
    biological papers that share generic ML/control vocabulary.
    """

    haystack = f"{paper.title} {paper.abstract}".lower()
    excluded = _matching_keywords(profile.get("exclude_keywords", []), haystack)
    article_type = paper.raw.get("paper_radar_article_type", {}) if isinstance(paper.raw, dict) else {}
    primary_research_ok = not isinstance(article_type, dict) or article_type.get("accepted", True)
    platform_evidence = _strict_platform_evidence(profile, haystack)
    task_evidence = _embodied_task_evidence(profile, haystack)
    venue_ok = (
        paper.source == "arxiv"
        or not profile.get("preferred_venues")
        or matches_preferred_venue(paper.venue or "", profile["preferred_venues"])
    )
    accepted = bool(venue_ok and not excluded and primary_research_ok and platform_evidence and task_evidence)
    if not primary_research_ok:
        reason = "non_research_article_type"
    elif excluded:
        reason = "excluded_domain"
    elif not venue_ok:
        reason = "venue_not_allowed"
    elif not platform_evidence:
        reason = "missing_embodied_platform"
    elif not task_evidence:
        reason = "missing_embodied_task"
    else:
        reason = "explicit_embodied_platform_and_task"
    return {
        "paper_id": f"{paper.source}:{paper.source_id}",
        "title": paper.title,
        "venue": paper.venue,
        "accepted": accepted,
        "category": "direct_embodied" if accepted else "rejected",
        "platform_evidence": platform_evidence,
        "task_evidence": task_evidence,
        "excluded_keywords": excluded,
        "article_type": article_type.get("article_type", "") if isinstance(article_type, dict) else "",
        "reason": reason,
    }


def _strict_platform_evidence(profile: Dict[str, Any], haystack: str) -> List[str]:
    keywords = profile.get("strict_platform_keywords")
    if not keywords:
        # Small test/extension profiles written before the strict schema keep
        # the same two-evidence rule by treating their legacy platform group as
        # the explicit platform vocabulary.  The production profile always
        # provides `strict_platform_keywords`.
        keywords = profile.get("required_signal_groups", {}).get("platform", [])
    return _matching_keywords(keywords, haystack)


def _embodied_task_evidence(profile: Dict[str, Any], haystack: str) -> List[str]:
    keywords = profile.get("embodied_task_keywords")
    if not keywords:
        groups = profile.get("required_signal_groups", {})
        keywords = list(groups.get("perception", [])) + list(groups.get("robotics", []))
    return _matching_keywords(keywords, haystack)


def _matching_keywords(keywords: Iterable[str], haystack: str) -> List[str]:
    return [keyword for keyword in keywords if _keyword_in_text(keyword, haystack)]


def _keyword_in_text(keyword: str, haystack: str) -> bool:
    needle = " ".join((keyword or "").lower().split())
    text = " ".join((haystack or "").lower().split())
    if not needle:
        return False
    if len(needle) <= 3 or " " not in needle:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text) is not None
    return needle in text
