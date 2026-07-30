from typing import Iterable, List

from .models import ScoredPaper
from .venues import matches_preferred_venue


def is_official_source(source: str) -> bool:
    return source != "arxiv"


def select_digest(
    scored_papers: Iterable[ScoredPaper],
    target_count: int,
    direct_min: int,
    direct_max: int,
    official_min: int = 0,
    preprint_max: int | None = None,
    tier1_broad_venues=None,
    tier2_venues=None,
    tier1_target: int | None = None,
    tier2_min: int = 0,
    tier2_max: int | None = None,
    preprint_min: int = 0,
    max_transferable: int | None = None,
) -> List[ScoredPaper]:
    ranked = rank_candidates(scored_papers)
    if tier1_broad_venues and tier1_target is not None:
        return _select_tiered_digest(
            ranked,
            target_count=target_count,
            tier1_broad_venues=tier1_broad_venues,
            tier2_venues=tier2_venues,
            tier1_target=tier1_target,
            tier2_min=tier2_min,
            tier2_max=tier2_max or max(0, target_count - tier1_target),
            preprint_min=preprint_min,
            preprint_max=preprint_max,
            direct_min=direct_min,
            max_transferable=max_transferable,
        )

    direct = [item for item in ranked if item.paper.paper_type == "direct"]
    transferable = [item for item in ranked if item.paper.paper_type != "direct"]
    official = [item for item in ranked if is_official_source(item.paper.source)]

    selected: List[ScoredPaper] = []
    for item in official:
        if len([paper for paper in selected if is_official_source(paper.paper.source)]) >= official_min:
            break
        if _can_add(item, selected, preprint_max):
            selected.append(item)
    for item in direct:
        if len([paper for paper in selected if paper.paper.paper_type == "direct"]) >= direct_max:
            break
        if _can_add(item, selected, preprint_max):
            selected.append(item)

    selected = _unique(selected)

    remaining_slots = target_count - len(selected)
    if remaining_slots > 0:
        for item in transferable:
            if item in selected or not _can_add(item, selected, preprint_max):
                continue
            selected.append(item)
            if len(selected) == target_count:
                break

    if len([item for item in selected if item.paper.paper_type == "direct"]) < direct_min:
        needed = direct_min - len([item for item in selected if item.paper.paper_type == "direct"])
        replacements = direct[direct_max : direct_max + needed]
        for replacement in replacements:
            if not _can_add(replacement, selected, preprint_max):
                continue
            if not selected:
                break
            for idx in range(len(selected) - 1, -1, -1):
                if selected[idx].paper.paper_type != "direct":
                    selected[idx] = replacement
                    break

    unique = []
    seen = set()
    for item in selected + ranked:
        key = (item.paper.title.lower(), item.paper.doi or "", item.paper.source_id)
        if key in seen:
            continue
        if not _can_add(item, unique, preprint_max):
            continue
        unique.append(item)
        seen.add(key)
        if len(unique) == target_count:
            break
    return unique


def rank_candidates(scored_papers: Iterable[ScoredPaper]) -> List[ScoredPaper]:
    """Rank relevance before freshness.

    Freshness is useful only after a paper has cleared the embodied-intelligence
    bar.  Sorting by publication date first let very recent but marginal papers
    crowd out a stronger robot-perception candidate with better evidence.
    """

    return sorted(
        scored_papers,
        key=lambda item: (
            bool(
                isinstance(item.paper.raw, dict)
                and isinstance(item.paper.raw.get("paper_radar_local_download"), dict)
                and item.paper.raw["paper_radar_local_download"].get("priority")
            ),
            item.score.relevance_score,
            item.score.total_score,
            item.paper.published_at,
        ),
        reverse=True,
    )


def _select_tiered_digest(
    ranked: List[ScoredPaper],
    target_count: int,
    tier1_broad_venues,
    tier2_venues,
    tier1_target: int,
    tier2_min: int,
    tier2_max: int,
    preprint_min: int,
    preprint_max: int | None,
    direct_min: int,
    max_transferable: int | None,
) -> List[ScoredPaper]:
    selected: List[ScoredPaper] = []
    tier1 = [item for item in ranked if _selection_bucket(item, tier1_broad_venues, tier2_venues) == "tier1"]
    tier2 = [item for item in ranked if _selection_bucket(item, tier1_broad_venues, tier2_venues) == "tier2"]
    preprints = [item for item in ranked if _selection_bucket(item, tier1_broad_venues, tier2_venues) == "preprint"]
    eligible_ranked = [
        item
        for item in ranked
        if _selection_bucket(item, tier1_broad_venues, tier2_venues) in {"tier1", "tier2", "preprint"}
    ]

    _append_from(tier1, selected, tier1_target, preprint_max)
    _append_from(tier2, selected, tier2_max, preprint_max)
    _append_from(preprints, selected, preprint_min, preprint_max)

    if _count_bucket(selected, "tier2", tier1_broad_venues, tier2_venues) < tier2_min:
        _append_from(
            tier2,
            selected,
            tier2_min - _count_bucket(selected, "tier2", tier1_broad_venues, tier2_venues),
            preprint_max,
        )
    if _count_bucket(selected, "preprint", tier1_broad_venues, tier2_venues) < preprint_min:
        _append_from(
            preprints,
            selected,
            preprint_min - _count_bucket(selected, "preprint", tier1_broad_venues, tier2_venues),
            preprint_max,
        )

    _append_from(eligible_ranked, selected, target_count - len(selected), preprint_max)
    result = rank_candidates(_unique(selected))[:target_count]
    tier1_count = _count_bucket(result, "tier1", tier1_broad_venues, tier2_venues)
    tier2_count = _count_bucket(result, "tier2", tier1_broad_venues, tier2_venues)
    preprint_count = _count_bucket(result, "preprint", tier1_broad_venues, tier2_venues)
    direct_count = sum(item.paper.paper_type == "direct" for item in result)
    transferable_count = len(result) - direct_count
    if tier1_count != tier1_target:
        return []
    if not tier2_min <= tier2_count <= tier2_max:
        return []
    if not preprint_min <= preprint_count <= (preprint_max if preprint_max is not None else target_count):
        return []
    if direct_count < direct_min:
        return []
    if max_transferable is not None and transferable_count > max_transferable:
        return []
    return result


def _append_from(
    candidates: List[ScoredPaper],
    selected: List[ScoredPaper],
    limit: int,
    preprint_max: int | None,
) -> None:
    if limit <= 0:
        return
    seen = {_identity(item) for item in selected}
    added = 0
    for item in candidates:
        if _identity(item) in seen or not _can_add(item, selected, preprint_max):
            continue
        selected.append(item)
        seen.add(_identity(item))
        added += 1
        if added >= limit:
            return


def _selection_bucket(item: ScoredPaper, tier1_broad_venues, tier2_venues=None) -> str:
    if item.paper.source == "arxiv":
        return "preprint"
    if matches_preferred_venue(item.paper.venue or "", tier1_broad_venues):
        return "tier1"
    if tier2_venues is not None and matches_preferred_venue(item.paper.venue or "", tier2_venues):
        return "tier2"
    if tier2_venues is not None:
        return "other"
    return "tier2"


def _count_bucket(items: List[ScoredPaper], bucket: str, tier1_broad_venues, tier2_venues=None) -> int:
    return sum(1 for item in items if _selection_bucket(item, tier1_broad_venues, tier2_venues) == bucket)


def _identity(item: ScoredPaper) -> tuple[str, str, str]:
    return (item.paper.title.lower(), item.paper.doi or "", item.paper.source_id)


def _can_add(item: ScoredPaper, selected: List[ScoredPaper], preprint_max: int | None) -> bool:
    if preprint_max is None:
        return True
    if item.paper.source != "arxiv":
        return True
    return sum(1 for existing in selected if existing.paper.source == "arxiv") < preprint_max


def _unique(items: List[ScoredPaper]) -> List[ScoredPaper]:
    unique = []
    seen = set()
    for item in items:
        key = _identity(item)
        if key in seen:
            continue
        unique.append(item)
        seen.add(key)
    return unique
