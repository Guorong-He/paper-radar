import os
import sys
import json
import re
import signal
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

from .config import load_profile
from .analysis import analyze_selected, choose_analysis_provider
from .article_type import primary_research_audit
from .db import (
    init_db,
    load_candidate_papers,
    paper_pk,
    upsert_analyses,
    upsert_papers,
    upsert_scores,
)
from .export import export_analyses_json, export_digest
from .figures import figure_one_audit, materialize_key_figures
from .fixtures import sample_papers
from .fulltext import fetch_fulltext
from .models import Paper, ScoredPaper
from .local_downloads import ingest_recent_downloads
from .myloft import (
    enqueue_candidate,
    exhausted_recovery_venues,
    has_exhausted_recovery_paths,
    has_imported_recovery,
    reconcile_pending_candidates,
)
from .myloft import queue_status as myloft_queue_status
from .packet import export_research_packet
from .rendering import render_outputs
from .scoring import score_papers
from .scansci_recovery import reset_recovery_audit
from .selection import _count_bucket, is_official_source, rank_candidates, select_digest
from .sources import arxiv, crossref, openalex, publisher_toc
from .tagging import candidate_audit, enrich_papers, passes_candidate_filter
from .http import probe_url
from .venues import matches_preferred_venue
from .state import (
    atomic_write_json,
    begin_run,
    deserialize_scored_paper,
    issue_state_path,
    load_candidate_cache,
    load_latest_candidate_catalog,
    mark_stage,
    read_json,
    record_run_event,
    save_candidate_cache,
    serialize_scored_paper,
    utc_now,
)


def run(
    db_path: str,
    fixture: bool = False,
    analyze: bool = True,
    refresh_sources: bool | None = None,
) -> List[ScoredPaper]:
    today = current_run_date()
    if refresh_sources is None:
        refresh_sources = (os.getenv("PAPER_RADAR_REFRESH_SOURCES") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    run_id = begin_run(today, fixture=fixture, analyze=analyze)
    try:
        selected = _run_impl(
            db_path,
            fixture=fixture,
            analyze=analyze,
            refresh_sources=refresh_sources,
            run_id=run_id,
        )
    except Exception as exc:
        record_run_event(
            today,
            run_id,
            "run",
            "failed",
            {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        mark_stage(
            today,
            "run",
            "failed",
            {"run_id": run_id, "error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        raise
    target_count = int(load_profile()["selection"]["target_count"])
    run_status = "complete" if len(selected) == target_count else "partial"
    record_run_event(
        today,
        run_id,
        "run",
        run_status,
        {"selected_count": len(selected), "target_count": target_count},
    )
    mark_stage(
        today,
        "run",
        run_status,
        {"run_id": run_id, "selected_count": len(selected), "target_count": target_count},
    )
    return selected


def _run_impl(
    db_path: str,
    fixture: bool,
    analyze: bool,
    refresh_sources: bool,
    run_id: str,
) -> List[ScoredPaper]:
    today = current_run_date()
    profile = load_profile()
    init_db(db_path)
    reset_recovery_audit()
    source_status = {}
    mark_stage(today, "discovery", "in_progress", {"run_id": run_id})
    record_run_event(today, run_id, "discovery", "started")

    if fixture:
        papers = sample_papers(today)
        source_status = {"fixture": {"enabled": True, "ok": True, "reason": "fixture"}}
    else:
        papers, source_status = discover_candidates(
            profile,
            today,
            refresh_sources=refresh_sources,
            db_path=db_path,
        )
        if profile["selection"].get("exclude_previous_recommendations", True):
            papers = exclude_previously_recommended_papers(papers, today)
    record_run_event(
        today,
        run_id,
        "discovery",
        "completed",
        {"candidate_count": len(papers), "source_status": _compact_source_status(source_status)},
    )
    mark_stage(
        today,
        "discovery",
        "complete",
        {"candidate_count": len(papers), "source_status": _compact_source_status(source_status)},
    )

    enriched = enrich_papers(papers, profile)
    filtered = [paper for paper in enriched if passes_candidate_filter(paper, profile)]
    scored_all = score_papers(filtered, profile, today)
    annotate_recovery_metadata(scored_all, profile)
    scored = []
    for item in scored_all:
        if item.paper.paper_type == "direct":
            scored.append(item)
            continue
        if (
            item.score.total_score >= profile["selection"]["min_candidate_score"]
            and item.score.relevance_score >= profile["selection"]["min_transferable_relevance"]
        ):
            scored.append(item)
    # A newly downloaded PDF is strong user intent. Match it only against the
    # already strict current candidate pool, validate its identity, revive any
    # prematurely skipped recovery, and prioritize it inside its original Tier.
    if not fixture:
        ingest_recent_downloads(scored, scan_label="pre_selection")
    # Never allow a historical metadata-only recovery entry to bypass the
    # current strict embodied-platform/task audit.  Terminal queue records are
    # retained; only stale pending entries are retired.
    reconcile_pending_candidates([item.paper for item in scored])
    # First form a quota-correct, metadata-only provisional issue. A formal
    # paper must be allowed to reach the authorized MyLOFT publisher-download
    # path before a missing public PDF/Figure 1 can evict it in favour of easy-
    # to-fetch preprints. Historical recovery notes remain audit evidence, not
    # a permanent exclusion from the current strict candidate set. Once a
    # paper has nevertheless completed its one current MyLOFT publisher
    # attempt *and* the same-work public recovery has failed, it is no longer
    # a recoverable candidate. Exclude only that terminal state so strict
    # selection can replace it from the same Tier instead of reselecting and
    # re-running the same failed recovery on every preparation pass.
    recoverable_scored = exclude_terminal_recovery_failures(scored)
    metadata_selected = select_digest_requiring_primary_research(recoverable_scored, profile)
    if not fixture:
        late_downloads = ingest_recent_downloads(recoverable_scored, scan_label="pre_recovery")
        if late_downloads:
            metadata_selected = select_digest_requiring_primary_research(recoverable_scored, profile)
    metadata_selected = apply_frozen_issue_slots(
        metadata_selected,
        recoverable_scored,
        profile,
    )
    record_run_event(
        today,
        run_id,
        "selection",
        "completed" if len(metadata_selected) == profile["selection"]["target_count"] else "partial",
        {"selected_count": len(metadata_selected)},
    )
    mark_stage(
        today,
        "selection",
        "complete" if len(metadata_selected) == profile["selection"]["target_count"] else "partial",
        {"selected_count": len(metadata_selected)},
    )
    # The visible MyLOFT queue is limited to the current provisional issue,
    # after publisher article-type metadata has excluded News & Views and other
    # non-research records.  It must never receive an editorial merely because
    # it shares the venue and embodied-robotics vocabulary.
    reconcile_pending_candidates([item.paper for item in metadata_selected])
    # This is intentionally metadata-only and happens before the selected
    # papers' full texts are read for analysis.  Keep an auditable admission
    # trail rather than allowing venue prestige or a usable figure to mask a
    # relevance failure.
    write_candidate_audit([candidate_audit(item.paper, profile) for item in metadata_selected])
    require_key_figures = profile["selection"].get("require_key_figures", True)
    raw_figure_gate = (os.getenv("PAPER_RADAR_REQUIRE_KEY_FIGURES") or "").strip().lower()
    if raw_figure_gate:
        require_key_figures = raw_figure_gate not in {"0", "false", "no", "off"}
    if require_key_figures:
        selected = select_digest_requiring_key_figures(
            recoverable_scored,
            profile,
            provisional_selected=metadata_selected,
            reconcile_downloads=not fixture,
        )
    else:
        selected = metadata_selected
        hydrate_fulltexts(selected)
        materialize_key_figures(selected, paper_pk)
    # Figure extraction usually hydrates the PDF text as a side effect, but a
    # valid cached figure can skip that path. Ensure every selected formal paper
    # still gets the approved ScanSci recovery attempt when its full text is
    # missing.
    hydrate_fulltexts(selected)
    audit_records = [candidate_audit(item.paper, profile) for item in selected]
    write_candidate_audit(audit_records)
    figure_audit_records = [figure_one_audit(item.paper) for item in selected]
    write_figure_audit(figure_audit_records)

    upsert_papers(db_path, enriched)
    upsert_scores(db_path, scored)
    preserved_existing_packet = False
    if analyze:
        export_research_packet(selected, paper_pk)
    else:
        preserved_existing_packet = preserve_existing_complete_packet_when_incomplete(selected, profile)
        if not preserved_existing_packet:
            export_research_packet(selected, paper_pk)
    write_prepare_status(
        selected,
        profile,
        preserved_existing_packet,
        source_status=source_status,
        candidate_audit_records=audit_records,
        figure_audit_records=figure_audit_records,
        pre_figure_selection_count=len(metadata_selected),
    )
    completed_status = "complete" if len(selected) == profile["selection"]["target_count"] else "partial"
    record_run_event(
        today,
        run_id,
        "recovery",
        completed_status,
        {"completed_count": len(selected), "target_count": profile["selection"]["target_count"]},
    )
    mark_stage(
        today,
        "recovery",
        completed_status,
        {"completed_count": len(selected), "target_count": profile["selection"]["target_count"]},
    )
    if analyze:
        analyses = analyze_selected(selected, choose_analysis_provider(), paper_pk)
        upsert_analyses(db_path, analyses)
        export_analyses_json(analyses)
        export_digest(selected, analyses, paper_pk)
        render_outputs(selected, analyses, paper_pk, issue_date=today)
        record_run_event(today, run_id, "analysis_render", "completed", {"paper_count": len(selected)})
        mark_stage(today, "analysis_render", "complete", {"paper_count": len(selected)})
    return selected


def current_run_date() -> date:
    raw = os.getenv("PAPER_RADAR_RUN_DATE") or os.getenv("PAPER_RADAR_ISSUE_DATE")
    if raw:
        return date.fromisoformat(raw)
    return date.today()


def preserve_existing_complete_packet_when_incomplete(
    selected: List[ScoredPaper],
    profile,
    packet_path: str = "output/research_packet.json",
) -> bool:
    if profile["selection"].get("semantic_audit_required", False):
        # A strict rebuild must never silently retain a prior packet that might
        # have passed older, looser relevance rules.
        return False
    target_count = profile["selection"]["target_count"]
    if len(selected) >= target_count:
        return False
    if not has_complete_research_packet(packet_path, target_count):
        return False
    print(
        f"[packet:preserve] live selection produced {len(selected)} papers; "
        f"kept existing complete packet at {packet_path}",
        file=sys.stderr,
    )
    return True


def write_prepare_status(
    selected: List[ScoredPaper],
    profile,
    preserved_existing_packet: bool,
    source_status: dict | None = None,
    candidate_audit_records: list[dict] | None = None,
    figure_audit_records: list[dict] | None = None,
    pre_figure_selection_count: int | None = None,
    output_path: str = "output/prepare_status.json",
) -> dict:
    packet_path = "output/research_packet.json"
    target_count = profile["selection"]["target_count"]
    packet_count = research_packet_count(packet_path)
    packet_quality = research_packet_quality(packet_path, target_count)
    audit_records = candidate_audit_records or []
    direct_count = sum(record.get("category") == "direct_embodied" for record in audit_records)
    transferable_count = len(audit_records) - direct_count
    semantic_gate = {
        "paper_count": len(audit_records),
        "accepted_count": sum(bool(record.get("accepted")) for record in audit_records),
        "direct_count": direct_count,
        "transferable_count": transferable_count,
        "required_direct_min": profile["selection"].get("direct_min", 0),
        "max_transferable": profile["selection"].get("max_transferable", target_count),
        "complete": (
            len(audit_records) == target_count
            and all(record.get("accepted") for record in audit_records)
            and direct_count >= profile["selection"].get("direct_min", 0)
            and transferable_count <= profile["selection"].get("max_transferable", target_count)
        ),
    }
    figure_records = figure_audit_records or []
    figure_identity_gate = {
        "paper_count": len(figure_records),
        "verified_figure_one_count": sum(bool(record.get("accepted")) for record in figure_records),
        "required_count": target_count,
        "complete": (
            len(figure_records) == target_count
            and all(bool(record.get("accepted")) for record in figure_records)
        ),
    }
    blocked_reason = ""
    if source_status and all_configured_sources_unreachable(source_status):
        blocked_reason = "all_configured_sources_unreachable"
    elif preserved_existing_packet:
        blocked_reason = "preserved_existing_packet"
    elif (
        profile["selection"].get("require_verified_figure_one", False)
        and pre_figure_selection_count == target_count
        and not figure_identity_gate["complete"]
    ):
        blocked_reason = "figure_one_identity_failed"
    elif not semantic_gate["complete"]:
        blocked_reason = "semantic_audit_failed"
    payload = {
        "target_count": target_count,
        "live_selection_count": len(selected),
        "pre_figure_selection_count": pre_figure_selection_count,
        "packet_count": packet_count,
        "preserved_existing_packet": preserved_existing_packet,
        "ready_to_publish": (
            not preserved_existing_packet
            and len(selected) == target_count
            and packet_count == target_count
            and packet_quality["complete"]
            and semantic_gate["complete"]
            and (
                not profile["selection"].get("require_verified_figure_one", False)
                or figure_identity_gate["complete"]
            )
        ),
        "quality_gate": packet_quality,
        "semantic_gate": semantic_gate,
        "figure_identity_gate": figure_identity_gate,
        "blocked_reason": blocked_reason,
        "source_status": source_status or {},
        "myloft": myloft_queue_status(),
    }
    atomic_write_json(output_path, payload)
    return payload


def write_candidate_audit(records: list[dict], output_path: str = "output/candidate_audit.json") -> None:
    """Persist metadata-only admission evidence for human final review."""

    atomic_write_json(output_path, records)


def write_figure_audit(records: list[dict], output_path: str = "output/figure_audit.json") -> None:
    """Persist strict Figure 1 identity and image-quality evidence."""

    atomic_write_json(output_path, records)


def has_complete_research_packet(packet_path: str, target_count: int) -> bool:
    path = Path(packet_path)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, list) or len(payload) != target_count:
        return False
    for item in payload:
        if not isinstance(item, dict):
            return False
        figure = item.get("key_figure_path")
        if not figure or not Path(figure).exists():
            return False
    return True


def research_packet_count(packet_path: str) -> int:
    path = Path(packet_path)
    if not path.exists():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def research_packet_quality(packet_path: str, target_count: int) -> dict:
    """Report the non-negotiable local-analysis inputs for a packet."""

    path = Path(packet_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = []
    if not isinstance(payload, list):
        payload = []
    figure_count = sum(
        1
        for item in payload
        if isinstance(item, dict)
        and item.get("key_figure_path")
        and Path(item["key_figure_path"]).is_file()
    )
    fulltext_count = sum(
        1
        for item in payload
        if isinstance(item, dict) and len((item.get("fulltext") or "").strip()) >= 1000
    )
    figure_one_count = sum(
        1
        for item in payload
        if isinstance(item, dict)
        and item.get("key_figure_path")
        and figure_one_audit(
            Paper(
                source=item.get("source", ""),
                source_id=item.get("paper_id", "").split(":", 1)[-1],
                title=item.get("title", ""),
                abstract="",
                authors=[],
                published_at=date.today(),
                key_figure_path=item.get("key_figure_path", ""),
                key_figure_caption=item.get("key_figure_caption", ""),
            )
        )["accepted"]
    )
    return {
        "figure_count": figure_count,
        "verified_figure_one_count": figure_one_count,
        "fulltext_count": fulltext_count,
        "required_count": target_count,
        "complete": (
            len(payload) == target_count
            and figure_count == target_count
            and figure_one_count == target_count
            and fulltext_count == target_count
        ),
    }


def discover_candidates(
    profile,
    today: date,
    *,
    refresh_sources: bool = False,
    db_path: str = "data/paper_radar.db",
) -> tuple[List[Paper], dict]:
    """Reuse same-issue results and incrementally refresh the 3-year catalog."""

    if not refresh_sources:
        current = load_candidate_cache(today)
        if current and current[0]:
            cached_papers, metadata = current
            papers, rejected = filter_candidate_publication_dates(cached_papers, profile, today)
            status = {
                "candidate_cache": {
                    "enabled": True,
                    "ok": bool(papers),
                    "cache_hit": True,
                    "catalog_issue_date": metadata.get("issue_date"),
                    "generated_at": metadata.get("generated_at"),
                    "paper_count": len(papers),
                }
            }
            status["publication_date_gate"] = _publication_date_gate_status(
                len(cached_papers),
                len(papers),
                rejected,
                profile,
                today,
            )
            if rejected:
                save_candidate_cache(today, papers, status)
            write_source_status(status)
            print(f"[fetch:cache-hit] papers={len(papers)}", file=sys.stderr)
            return papers, status

    prior = None if refresh_sources else load_latest_candidate_catalog(today)
    if prior is None and not refresh_sources:
        bootstrap_papers = _load_database_candidate_catalog(db_path, profile, today)
        if bootstrap_papers:
            prior = (
                bootstrap_papers,
                {
                    "issue_date": None,
                    "generated_at": None,
                    "catalog_source": "database_bootstrap",
                },
            )
    if prior:
        raw_base_papers, metadata = prior
        base_papers, base_rejected = filter_candidate_publication_dates(
            raw_base_papers,
            profile,
            today,
        )
        incremental_profile = deepcopy(profile)
        selection = incremental_profile.setdefault("selection", {})
        incremental_days = max(1, int(selection.get("candidate_incremental_days", 21)))
        selection["backfill_days"] = incremental_days
        selection["official_backfill_days"] = incremental_days
        recent_papers, source_status = fetch_live_candidates(incremental_profile, today)
        papers = dedupe_papers([*base_papers, *recent_papers])
        papers, merged_rejected = filter_candidate_publication_dates(papers, profile, today)
        source_status["candidate_cache"] = {
            "enabled": True,
            "ok": True,
            "cache_hit": False,
            "incremental_refresh": True,
            "catalog_issue_date": metadata.get("issue_date"),
            "catalog_source": metadata.get("catalog_source") or "issue_cache",
            "base_count": len(base_papers),
            "recent_count": len(recent_papers),
            "paper_count": len(papers),
        }
        rejected = base_rejected + merged_rejected
        source_status["publication_date_gate"] = _publication_date_gate_status(
            len(raw_base_papers) + len(recent_papers),
            len(papers),
            rejected,
            profile,
            today,
        )
    else:
        papers, source_status = fetch_live_candidates(profile, today)
        source_status["candidate_cache"] = {
            "enabled": True,
            "ok": bool(papers),
            "cache_hit": False,
            "incremental_refresh": False,
            "full_refresh": True,
            "paper_count": len(papers),
        }

    # Do not turn a transient all-source outage into a sticky empty cache. An
    # empty result must probe the sources again on the next retry.
    if papers:
        save_candidate_cache(today, papers, source_status)
    write_source_status(source_status)
    return papers, source_status


def warm_candidate_cache_from_database(
    db_path: str,
    profile,
    issue_date: date,
    *,
    force: bool = False,
) -> dict:
    """Create a prior-issue metadata catalog without a network fetch."""

    existing = load_candidate_cache(issue_date)
    if existing and existing[0] and not force:
        return {
            "issue_date": issue_date.isoformat(),
            "status": "already_warm",
            "paper_count": len(existing[0]),
            "path": existing[1].get("path"),
        }
    papers = _load_database_candidate_catalog(db_path, profile, issue_date)
    if not papers:
        raise RuntimeError("No bounded candidate metadata is available in the database")
    source_status = {
        "database_bootstrap": {
            "enabled": True,
            "ok": True,
            "database": str(db_path),
            "paper_count": len(papers),
        },
        "publication_date_gate": _publication_date_gate_status(
            len(papers),
            len(papers),
            [],
            profile,
            issue_date,
        ),
    }
    path = save_candidate_cache(issue_date, papers, source_status)
    return {
        "issue_date": issue_date.isoformat(),
        "status": "warmed",
        "paper_count": len(papers),
        "path": str(path),
    }


def _load_database_candidate_catalog(db_path: str, profile, today: date) -> List[Paper]:
    lower, upper = candidate_publication_bounds(profile, today)
    papers = load_candidate_papers(db_path, lower, upper)
    filtered, _ = filter_candidate_publication_dates(papers, profile, today)
    return dedupe_papers(filtered)


def candidate_publication_bounds(profile, today: date) -> tuple[date, date]:
    selection = profile.get("selection", {}) if isinstance(profile, dict) else {}
    lookback_days = int(selection.get("lookback_days", 1095))
    grace_days = max(0, int(selection.get("future_publication_grace_days", 7)))
    return today - timedelta(days=lookback_days), today + timedelta(days=grace_days)


def filter_candidate_publication_dates(
    papers: Sequence[Paper],
    profile,
    today: date,
) -> tuple[List[Paper], list[dict]]:
    lower, upper = candidate_publication_bounds(profile, today)
    accepted = []
    rejected = []
    for paper in papers:
        if lower <= paper.published_at <= upper:
            accepted.append(paper)
            continue
        rejected.append(
            {
                "paper_id": paper.doi or f"{paper.source}:{paper.source_id}",
                "published_at": paper.published_at.isoformat(),
                "reason": "before_lookback" if paper.published_at < lower else "future_date",
            }
        )
    return accepted, rejected


def _publication_date_gate_status(
    input_count: int,
    accepted_count: int,
    rejected: list[dict],
    profile,
    today: date,
) -> dict:
    lower, upper = candidate_publication_bounds(profile, today)
    return {
        "enabled": True,
        "ok": True,
        "input_count": input_count,
        "accepted_count": accepted_count,
        "rejected_count": len(rejected),
        "future_rejected_count": sum(item.get("reason") == "future_date" for item in rejected),
        "published_from": lower.isoformat(),
        "published_until": upper.isoformat(),
    }


def _compact_source_status(source_status: dict) -> dict:
    return {
        name: {
            key: status.get(key)
            for key in ("enabled", "ok", "status_code", "attempts", "cache_hit", "paper_count")
            if key in status
        }
        for name, status in source_status.items()
        if isinstance(status, dict)
    }


def fetch_live_candidates(profile, today: date) -> tuple[List[Paper], dict]:
    from_date = today - timedelta(days=profile["selection"]["backfill_days"])
    official_from_date = today - timedelta(days=profile["selection"]["official_backfill_days"])
    _, until_date = candidate_publication_bounds(profile, today)
    contact_email = os.getenv("PAPER_RADAR_CONTACT_EMAIL", "")
    papers: List[Paper] = []
    source_status = probe_live_sources(profile, today)
    write_source_status(source_status)
    if all_configured_sources_unreachable(source_status):
        print("[fetch:blocked] all configured sources unreachable after source probe", file=sys.stderr)
        return [], source_status

    if source_status.get("crossref", {}).get("ok"):
        if source_status.get("publisher_toc", {}).get("ok"):
            toc_candidates = publisher_toc.fetch_candidate_dois(
                profile.get("publisher_toc_feeds", []),
                official_from_date,
                _publisher_toc_relevance_terms(profile),
                max_items_per_feed=profile["selection"].get("max_publisher_toc_items_per_feed", 30),
                timeout=profile["selection"].get("publisher_toc_timeout_seconds", 10),
            )
            toc_doi_jobs = [
                (
                    candidate.doi,
                    lambda doi=candidate.doi: [crossref.fetch_work_by_doi(doi, mailto=contact_email)],
                )
                for candidate in toc_candidates
            ]
            papers.extend(
                _run_fetch_jobs(
                    toc_doi_jobs,
                    label="publisher_toc:doi",
                    concurrency=_fetch_concurrency("PAPER_RADAR_PUBLISHER_TOC_CONCURRENCY", default=3),
                )
            )
        elif source_status.get("publisher_toc", {}).get("enabled"):
            print("[publisher_toc:skip] source probe did not pass", file=sys.stderr)

        doi_jobs = [
            (
                doi,
                lambda doi=doi: [crossref.fetch_work_by_doi(doi, mailto=contact_email)],
            )
            for doi in profile.get("must_watch_dois", [])
        ]
        papers.extend(
            _run_fetch_jobs(
                doi_jobs,
                label="crossref:doi",
                concurrency=_fetch_concurrency("PAPER_RADAR_CROSSREF_CONCURRENCY", default=3),
            )
        )
        venue_jobs = [
            (
                venue,
                lambda venue=venue: crossref.fetch_recent_journal_works(
                    venue,
                    official_from_date,
                    rows=_venue_recall_limit(profile, venue),
                    mailto=contact_email,
                    until_date=until_date,
                ),
            )
            for venue in profile.get("venue_watchlist", [])
        ]
        papers.extend(
            _run_fetch_jobs(
                venue_jobs,
                label="crossref:venue",
                concurrency=_fetch_concurrency("PAPER_RADAR_CROSSREF_CONCURRENCY", default=3),
            )
        )
        tier2_targeted_jobs = [
            (
                f"{venue} | {query}",
                lambda venue=venue, query=query: crossref.fetch_recent_journal_query(
                    venue,
                    query,
                    official_from_date,
                    rows=profile["selection"].get("max_tier2_targeted_results_per_query", 50),
                    mailto=contact_email,
                    until_date=until_date,
                ),
            )
            for venue in profile.get("tier2_targeted_recall_venues", [])
            for query in profile.get("tier2_targeted_recall_queries", [])
        ]
        papers.extend(
            _run_fetch_jobs(
                tier2_targeted_jobs,
                label="crossref:tier2_targeted",
                concurrency=_fetch_concurrency("PAPER_RADAR_CROSSREF_CONCURRENCY", default=3),
            )
        )
        query_jobs = [
            (
                query,
                lambda query=query: crossref.fetch_recent_query(
                    query,
                    official_from_date,
                    rows=profile["selection"]["max_authority_results_per_query"],
                    mailto=contact_email,
                    until_date=until_date,
                ),
            )
            for query in profile.get("authority_queries", [])
        ]
        papers.extend(
            _run_fetch_jobs(
                query_jobs,
                label="crossref:query",
                concurrency=_fetch_concurrency("PAPER_RADAR_CROSSREF_CONCURRENCY", default=3),
            )
        )
    else:
        print("[crossref:skip] source probe did not pass", file=sys.stderr)
        if source_status.get("publisher_toc", {}).get("enabled"):
            print("[publisher_toc:skip] Crossref DOI enrichment unavailable", file=sys.stderr)

    if source_status.get("openalex", {}).get("ok"):
        openalex_jobs = [
            (
                query,
                lambda query=query: openalex.fetch_recent(
                    query,
                    from_date,
                    mailto=contact_email,
                    until_date=until_date,
                ),
            )
            for query in profile["queries"]["openalex"]
        ]
        papers.extend(
            _run_fetch_jobs(
                openalex_jobs,
                label="openalex",
                concurrency=_fetch_concurrency("PAPER_RADAR_OPENALEX_CONCURRENCY", default=4),
            )
        )
    elif source_status.get("openalex", {}).get("enabled"):
        print("[openalex:skip] source probe did not pass", file=sys.stderr)

    if source_status.get("arxiv", {}).get("ok"):
        consecutive_arxiv_failures = 0
        for query in profile["queries"]["arxiv"]:
            try:
                print(f"[arxiv] {query}", file=sys.stderr)
                query_papers = arxiv.fetch_recent(
                    query,
                    max_results=profile["selection"]["max_results_per_query"],
                )
                papers.extend(query_papers)
                consecutive_arxiv_failures = 0
                print(
                    f"[arxiv:ok] {query} added={len(query_papers)} total={len(papers)}",
                    file=sys.stderr,
                )
            except Exception as exc:
                consecutive_arxiv_failures += 1
                print(f"[arxiv:error] {query}: {exc}", file=sys.stderr)
                if consecutive_arxiv_failures >= _arxiv_circuit_failure_limit():
                    print(
                        f"[arxiv:circuit-break] consecutive_failures={consecutive_arxiv_failures}; "
                        "skipping remaining queries for this run",
                        file=sys.stderr,
                    )
                    source_status["arxiv"]["ok"] = False
                    source_status["arxiv"]["reason"] = "query circuit breaker"
                    break
                continue
    elif source_status.get("arxiv", {}).get("enabled"):
        print("[arxiv:skip] source probe did not pass", file=sys.stderr)
    date_filtered, rejected_dates = filter_candidate_publication_dates(
        papers,
        profile,
        today,
    )
    deduped = dedupe_papers(date_filtered)
    source_status["publication_date_gate"] = _publication_date_gate_status(
        len(papers),
        len(date_filtered),
        rejected_dates,
        profile,
        today,
    )
    write_source_status(source_status)
    print(f"[fetch:done] raw={len(papers)} deduped={len(deduped)}", file=sys.stderr)
    return deduped, source_status


def probe_live_sources(profile, today: date) -> dict[str, dict]:
    retries = _source_probe_retries()
    enabled = _enabled_source_probes(profile, today)
    if not enabled:
        return {}

    final_status = {name: dict(status) for name, status in enabled.items()}
    enabled_count = sum(1 for status in enabled.values() if status.get("enabled"))
    for attempt in range(1, retries + 1):
        ok_count = 0
        for source_name, status in enabled.items():
            if not status.get("enabled"):
                final_status[source_name] = dict(status)
                continue
            try:
                status_code = probe_url(
                    status["url"],
                    headers={"User-Agent": "paper-radar/0.1"},
                    timeout=_source_probe_timeout_seconds(),
                )
                final_status[source_name] = {
                    **status,
                    "ok": True,
                    "status_code": status_code,
                    "attempts": attempt,
                    "error": "",
                }
                ok_count += 1
                print(
                    f"[probe:{source_name}:ok] attempt={attempt}/{retries} status={status_code}",
                    file=sys.stderr,
                )
            except Exception as exc:
                final_status[source_name] = {
                    **status,
                    "ok": False,
                    "status_code": None,
                    "attempts": attempt,
                    "error": str(exc),
                }
                print(
                    f"[probe:{source_name}:error] attempt={attempt}/{retries} error={exc}",
                    file=sys.stderr,
                )
        if ok_count == enabled_count:
            break
        if attempt < retries:
            delay_seconds = _source_probe_delay_seconds(attempt)
            retry_reason = "all" if ok_count == 0 else "some"
            print(
                f"[probe:retry] {retry_reason} enabled sources unreachable; sleeping {delay_seconds:.1f}s before retry",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)
    return final_status


def write_source_status(source_status: dict, output_path: str = "output/source_status.json") -> None:
    if output_path == "output/source_status.json":
        output_path = os.getenv("PAPER_RADAR_SOURCE_STATUS_PATH") or output_path
    atomic_write_json(output_path, source_status)


def all_configured_sources_unreachable(source_status: dict) -> bool:
    enabled = [status for status in source_status.values() if isinstance(status, dict) and status.get("enabled")]
    return bool(enabled) and not any(status.get("ok") for status in enabled)


def _enabled_source_probes(profile, today: date) -> dict[str, dict]:
    statuses = {}
    contact_email = os.getenv("PAPER_RADAR_CONTACT_EMAIL", "")
    if (
        profile.get("venue_watchlist")
        or profile.get("authority_queries")
        or profile.get("must_watch_dois")
        or profile.get("publisher_toc_feeds")
    ):
        _, until_date = candidate_publication_bounds(profile, today)
        crossref_url = (
            f"{crossref.BASE_URL}?rows=0&filter=from-pub-date:{today.isoformat()},"
            f"until-pub-date:{until_date.isoformat()}"
        )
        if contact_email:
            crossref_url += f"&mailto={contact_email}"
        statuses["crossref"] = {"enabled": True, "url": crossref_url}
    else:
        statuses["crossref"] = {"enabled": False, "ok": False, "reason": "no crossref watchlist or authority queries"}

    if os.getenv("PAPER_RADAR_DISABLE_OPENALEX", "0") == "1":
        statuses["openalex"] = {"enabled": False, "ok": False, "reason": "disabled by env"}
    elif profile.get("queries", {}).get("openalex"):
        _, until_date = candidate_publication_bounds(profile, today)
        openalex_url = (
            f"{openalex.BASE_URL}?search=robot&per-page=1&filter="
            f"from_publication_date:{today.isoformat()},to_publication_date:{until_date.isoformat()}"
        )
        if contact_email:
            openalex_url += f"&mailto={contact_email}"
        statuses["openalex"] = {"enabled": True, "url": openalex_url}
    else:
        statuses["openalex"] = {"enabled": False, "ok": False, "reason": "no openalex queries"}

    if profile.get("queries", {}).get("arxiv"):
        statuses["arxiv"] = {
            "enabled": True,
            "url": f"{arxiv.BASE_URL}?search_query=all:robot&start=0&max_results=1&sortBy=submittedDate&sortOrder=descending",
        }
    else:
        statuses["arxiv"] = {"enabled": False, "ok": False, "reason": "no arxiv queries"}

    if os.getenv("PAPER_RADAR_DISABLE_PUBLISHER_TOC", "0") == "1":
        statuses["publisher_toc"] = {"enabled": False, "ok": False, "reason": "disabled by env"}
    elif profile.get("publisher_toc_feeds"):
        first_feed = profile["publisher_toc_feeds"][0]
        statuses["publisher_toc"] = {"enabled": True, "url": first_feed.get("url", "")}
    else:
        statuses["publisher_toc"] = {"enabled": False, "ok": False, "reason": "no publisher toc feeds"}
    return statuses


def _publisher_toc_relevance_terms(profile) -> list[str]:
    configured = profile.get("publisher_toc_signals")
    if configured:
        return configured
    terms: list[str] = []
    for key in ("research_focus", "relevance_keywords", "user_priority_keywords"):
        terms.extend(profile.get(key, []))
    signal_groups = profile.get("required_signal_groups", {})
    for values in signal_groups.values():
        terms.extend(values)
    return sorted({term for term in terms if term})


def _venue_recall_limit(profile, venue: str) -> int:
    """Use the full configured backfill depth for scarce Tier 1 candidates.

    General venue queries remain bounded for routine latency.  The formal Tier
    1 quota is small and its relevant work is sparse, so its own configured
    depth prevents the latest unrelated articles from crowding out an eligible
    embodied-robotics paper elsewhere in the allowed three-year window.
    """

    selection = profile.get("selection", {})
    default = selection.get("max_authority_results_per_venue", 50)
    tier1_venues = profile.get("tier1_broad_venues", [])
    if matches_preferred_venue(venue or "", tier1_venues):
        return selection.get("max_tier1_authority_results_per_venue", default)
    return default


def _source_probe_retries() -> int:
    raw = (os.getenv("PAPER_RADAR_SOURCE_PROBE_RETRIES") or "").strip()
    try:
        return max(1, int(raw)) if raw else 3
    except ValueError:
        return 3


def _source_probe_timeout_seconds() -> int:
    raw = (os.getenv("PAPER_RADAR_SOURCE_PROBE_TIMEOUT_SECONDS") or "").strip()
    try:
        return max(1, int(raw)) if raw else 8
    except ValueError:
        return 8


def _source_probe_delay_seconds(attempt: int) -> float:
    raw = (os.getenv("PAPER_RADAR_SOURCE_PROBE_DELAY_SECONDS") or "").strip()
    try:
        base = float(raw) if raw else 20.0
    except ValueError:
        base = 20.0
    return max(1.0, base * attempt)


def _fetch_concurrency(env_name: str, default: int) -> int:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _arxiv_circuit_failure_limit() -> int:
    raw = (os.getenv("PAPER_RADAR_ARXIV_CIRCUIT_FAILURES") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _run_fetch_jobs(
    jobs: Sequence[Tuple[str, Callable[[], List[Paper]]]],
    label: str,
    concurrency: int,
) -> List[Paper]:
    if not jobs:
        return []

    print(f"[{label}:batch] jobs={len(jobs)} concurrency={concurrency}", file=sys.stderr)
    results: List[List[Paper]] = [[] for _ in jobs]
    futures = {}
    completed = 0
    raw_total = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        for index, (job_name, fetch_fn) in enumerate(jobs):
            print(f"[{label}] {job_name}", file=sys.stderr)
            futures[executor.submit(fetch_fn)] = (index, job_name)
        for future in as_completed(futures):
            index, job_name = futures[future]
            completed += 1
            try:
                papers = future.result()
                results[index] = papers
                raw_total += len(papers)
                print(
                    f"[{label}:ok] {job_name} added={len(papers)} completed={completed}/{len(jobs)} raw_total={raw_total}",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(
                    f"[{label}:error] {job_name}: {exc} completed={completed}/{len(jobs)} raw_total={raw_total}",
                    file=sys.stderr,
                )
    combined: List[Paper] = []
    for papers in results:
        combined.extend(papers)
    return combined


def dedupe_papers(papers: List[Paper]) -> List[Paper]:
    unique = []
    seen = set()
    for paper in papers:
        key = (
            _canonical_doi(paper.doi),
            paper.title.strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(paper)
    return unique


def exclude_previously_recommended_papers(
    papers: List[Paper],
    today: date,
    site_dir: str = "site",
) -> List[Paper]:
    previous_keys = load_previous_recommendation_keys(today, site_dir)
    if not previous_keys:
        return papers
    return [paper for paper in papers if not (_paper_history_keys(paper) & previous_keys)]


def load_previous_recommendation_keys(today: date, site_dir: str = "site") -> set[str]:
    index = _load_or_build_history_index(site_dir)
    keys: set[str] = set()
    for issue in index.get("issues", []):
        try:
            issue_date = date.fromisoformat(issue.get("issue_date", ""))
        except (TypeError, ValueError):
            continue
        if issue_date < today:
            keys.update(issue.get("keys", []))
    return keys


def check_research_packet_history(
    packet_path: str = "output/research_packet.json",
    today: date | None = None,
    site_dir: str = "site",
) -> dict:
    """Return a compact duplicate report without exposing historical packets.

    This is the only history result an automation or model needs to read.
    Full archived packets are parsed locally and reduced to canonical keys.
    """

    run_date = today or current_run_date()
    previous_keys = load_previous_recommendation_keys(run_date, site_dir)
    path = Path(packet_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = []
    if isinstance(payload, dict):
        payload = payload.get("papers", [])
    overlaps = []
    paper_count = 0
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        paper_count += 1
        matched = sorted(_packet_history_keys(item) & previous_keys)
        if matched:
            overlaps.append(
                {
                    "paper_id": item.get("paper_id") or "",
                    "title": item.get("title") or "",
                    "matched_keys": matched,
                }
            )
    return {
        "issue_date": run_date.isoformat(),
        "paper_count": paper_count,
        "history_key_count": len(previous_keys),
        "overlap_count": len(overlaps),
        "overlaps": overlaps,
    }


def _load_or_build_history_index(site_dir: str) -> dict:
    path = _history_index_path(site_dir)
    signature = _history_archive_signature(site_dir)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = {}
    if cached.get("schema_version") == 1 and cached.get("archive_signature") == signature:
        return cached
    built = _build_history_index(site_dir, signature)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
    return built


def _build_history_index(site_dir: str, signature: list | None = None) -> dict:
    issues = []
    issues_dir = Path(site_dir) / "issues"
    if not issues_dir.exists():
        return {"schema_version": 1, "archive_signature": signature or [], "issues": []}
    for packet_path in sorted(issues_dir.glob("*/research_packet.json")):
        try:
            issue_date = date.fromisoformat(packet_path.parent.name)
        except ValueError:
            continue
        try:
            payload = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            packet_items = payload.get("papers", [])
        else:
            packet_items = payload
        if not isinstance(packet_items, list):
            continue
        issue_keys: set[str] = set()
        for item in packet_items:
            if isinstance(item, dict):
                issue_keys.update(_packet_history_keys(item))
        issues.append({"issue_date": issue_date.isoformat(), "keys": sorted(issue_keys)})
    return {
        "schema_version": 1,
        "archive_signature": signature if signature is not None else _history_archive_signature(site_dir),
        "issues": issues,
    }


def _history_index_path(site_dir: str) -> Path:
    if Path(site_dir) == Path("site"):
        return Path("data/recommendation_history_index.json")
    return Path(site_dir) / ".recommendation_history_index.json"


def _history_archive_signature(site_dir: str) -> list[list[int | str]]:
    signature = []
    issues_dir = Path(site_dir) / "issues"
    for path in sorted(issues_dir.glob("*/research_packet.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append([str(path.relative_to(site_dir)), stat.st_size, stat.st_mtime_ns])
    return signature


def _paper_history_keys(paper: Paper) -> set[str]:
    keys = set()
    doi = _canonical_doi(paper.doi)
    if doi:
        keys.add(f"doi:{doi}")
    if paper.source and paper.source_id:
        keys.add(f"source:{paper.source.lower()}:{_canonical_source_id(paper.source, paper.source_id)}")
    title = _canonical_title(paper.title)
    if title:
        keys.add(f"title:{title}")
    return keys


def _packet_history_keys(item: dict) -> set[str]:
    keys = set()
    doi = _canonical_doi(item.get("doi"))
    if doi:
        keys.add(f"doi:{doi}")
    paper_id = item.get("paper_id") or ""
    if ":" in paper_id:
        source, source_id = paper_id.split(":", 1)
        if source and source_id:
            keys.add(f"source:{source.lower()}:{_canonical_source_id(source, source_id)}")
    title = _canonical_title(item.get("title") or "")
    if title:
        keys.add(f"title:{title}")
    return keys


def _canonical_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def _canonical_doi(doi: str | None) -> str:
    cleaned = (doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


def _canonical_source_id(source: str, source_id: str) -> str:
    cleaned = (source_id or "").strip().lower()
    if (source or "").lower() == "arxiv":
        cleaned = re.sub(r"v\d+$", "", cleaned)
    return cleaned


def hydrate_fulltexts(scored_papers: List[ScoredPaper]) -> None:
    for item in scored_papers:
        if not item.paper.fulltext and (item.paper.pdf_url or item.paper.source != "arxiv"):
            item.paper.fulltext = fetch_fulltext(item.paper) or ""


def annotate_recovery_metadata(scored_papers: List[ScoredPaper], profile) -> None:
    """Attach ranking context used only by the local recovery queue."""

    tier1_venues = profile.get("tier1_broad_venues", [])
    tier2_venues = profile.get("preferred_venues", [])
    for item in scored_papers:
        paper = item.paper
        if paper.source == "arxiv":
            tier = "preprint"
        elif matches_preferred_venue(paper.venue or "", tier1_venues):
            tier = "tier1"
        elif matches_preferred_venue(paper.venue or "", tier2_venues):
            tier = "tier2"
        else:
            tier = "other"
        paper.raw = dict(paper.raw or {})
        paper.raw["paper_radar_recovery"] = {
            "tier": tier,
            "total_score": item.score.total_score,
            "relevance_score": item.score.relevance_score,
            "published_at": paper.published_at.isoformat(),
        }


def exclude_terminal_recovery_failures(scored_papers: List[ScoredPaper]) -> List[ScoredPaper]:
    """Keep only candidates that still have a permitted recovery path.

    A recovery path is exhausted only after a formal paper has received both
    its current-issue direct publisher attempt and its approved public
    same-work recovery. This is deliberately narrower than historical queue
    metadata: pending records and earlier discovery notes remain eligible for
    their first current MyLOFT attempt.  When three papers from the exact same
    venue have reached that double-failure state, treat that venue as
    temporarily unavailable for the current issue and select a same-Tier paper
    from another configured formal venue instead.  This circuit breaker never
    changes relevance requirements, Tier definitions, or future issues.
    """

    unavailable_venues = exhausted_recovery_venues()
    return [
        item
        for item in scored_papers
        if item.paper.source == "arxiv"
        or not item.paper.doi
        or has_imported_recovery(item.paper.doi)
        or (
            not has_exhausted_recovery_paths(item.paper.doi)
            and " ".join((item.paper.venue or "").casefold().split()) not in unavailable_venues
        )
    ]


def select_digest_requiring_key_figures(
    scored_papers: List[ScoredPaper],
    profile,
    provisional_selected: List[ScoredPaper] | None = None,
    reconcile_downloads: bool = False,
) -> List[ScoredPaper]:
    """Recover the quota-correct issue before enforcing its Figure 1 gate.

    Selecting only from papers that already expose a public figure biases the
    issue toward arXiv.  Instead, select the strict Tier 1/Tier 2/preprint mix
    from metadata, recover its formal papers through the approved paths (and
    queue unresolved formal papers for visible MyLOFT), then extract Figure 1
    from those local PDFs. A terminal direct-plus-public recovery failure is
    excluded on the next run so a same-tier candidate replaces it.
    """

    target_count = profile["selection"]["target_count"]
    selected = provisional_selected or select_digest(
        scored_papers,
        target_count=target_count,
        direct_min=profile["selection"]["direct_min"],
        direct_max=profile["selection"]["direct_max"],
        official_min=profile["selection"]["official_min"],
        preprint_max=profile["selection"].get("preprint_max"),
        tier1_broad_venues=profile.get("tier1_broad_venues"),
        tier2_venues=profile.get("preferred_venues"),
        tier1_target=profile["selection"].get("tier1_target"),
        tier2_min=profile["selection"].get("tier2_min", 0),
        tier2_max=profile["selection"].get("tier2_max"),
        preprint_min=profile["selection"].get("preprint_min", 0),
        max_transferable=profile["selection"].get("max_transferable"),
    )
    if len(selected) != target_count:
        return []

    restore_frozen_issue_assets(selected)
    hydrate_fulltexts(selected)
    for index, item in enumerate(selected, start=1):
        print(
            f"[figures:start] processed={index}/{len(selected)} title={item.paper.title[:120]}",
            file=sys.stderr,
        )
        _materialize_key_figure_with_timeout(item)
        print(
            f"[figures] processed={index}/{len(selected)} "
            f"figure={'yes' if item.paper.key_figure_path else 'no'} "
            f"title={item.paper.title[:120]}",
            file=sys.stderr,
        )

    # A browser or manual download may finish while metadata recovery and
    # figure extraction are running. Reconcile once more before declaring a
    # slot incomplete, then retry only the still-incomplete selected papers.
    if reconcile_downloads and ingest_recent_downloads(scored_papers, scan_label="post_recovery"):
        hydrate_fulltexts(selected)
        for item in selected:
            if len((item.paper.fulltext or "").strip()) >= 1000 and figure_one_audit(item.paper).get("accepted"):
                continue
            _materialize_key_figure_with_timeout(item)

    require_verified_figure_one = profile["selection"].get("require_verified_figure_one", False)
    completed = [
        item
        for item in selected
        if len((item.paper.fulltext or "").strip()) >= 1000
        and (
            figure_one_audit(item.paper).get("accepted")
            if require_verified_figure_one
            else bool(item.paper.key_figure_path)
        )
    ]
    write_issue_working_set(selected, completed, profile)
    # Preserve completed slots even when another paper is still unresolved.
    # Publishing remains blocked until all target slots complete, but the next
    # pass can replace only the failed same-Tier slot instead of discarding the
    # work already completed for the rest of the issue.
    return completed


def select_digest_requiring_primary_research(
    scored_papers: List[ScoredPaper],
    profile,
) -> List[ScoredPaper]:
    """Keep the strict quota mix while replacing known editorials in-place."""

    excluded_ids: set[str] = set()
    target_count = profile["selection"]["target_count"]
    for _ in range(target_count + 1):
        selected = select_digest(
            [item for item in scored_papers if _selection_id(item.paper) not in excluded_ids],
            target_count=target_count,
            direct_min=profile["selection"]["direct_min"],
            direct_max=profile["selection"]["direct_max"],
            official_min=profile["selection"]["official_min"],
            preprint_max=profile["selection"].get("preprint_max"),
            tier1_broad_venues=profile.get("tier1_broad_venues"),
            tier2_venues=profile.get("preferred_venues"),
            tier1_target=profile["selection"].get("tier1_target"),
            tier2_min=profile["selection"].get("tier2_min", 0),
            tier2_max=profile["selection"].get("tier2_max"),
            preprint_min=profile["selection"].get("preprint_min", 0),
            max_transferable=profile["selection"].get("max_transferable"),
        )
        if len(selected) != target_count:
            return []
        rejected = []
        for item in selected:
            audit = primary_research_audit(item.paper)
            item.paper.raw = dict(item.paper.raw or {})
            item.paper.raw["paper_radar_article_type"] = audit
            if not audit.get("accepted"):
                rejected.append(item)
        if not rejected:
            return selected
        excluded_ids.update(_selection_id(item.paper) for item in rejected)
    return []


def _selection_id(paper: Paper) -> str:
    return (paper.doi or f"{paper.source}:{paper.source_id}").strip().lower()


def _issue_working_set_path() -> Path:
    return issue_state_path(current_run_date())


def _load_issue_working_set() -> dict:
    path = _issue_working_set_path()
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    if not isinstance(payload, dict) or payload.get("issue_date") != current_run_date().isoformat():
        return {}
    return payload


def _paper_bucket(paper: Paper, profile) -> str:
    if paper.source == "arxiv":
        return "preprint"
    metadata = paper.raw.get("paper_radar_recovery", {}) if isinstance(paper.raw, dict) else {}
    tier = metadata.get("tier") if isinstance(metadata, dict) else None
    if tier in {"tier1", "tier2"}:
        return tier
    if matches_preferred_venue(paper.venue or "", profile.get("tier1_broad_venues", [])):
        return "tier1"
    if matches_preferred_venue(paper.venue or "", profile.get("preferred_venues", [])):
        return "tier2"
    return "other"


def apply_frozen_issue_slots(
    selected: List[ScoredPaper],
    candidates: List[ScoredPaper],
    profile,
) -> List[ScoredPaper]:
    """Reinsert completed current-issue papers by replacing only their Tier."""

    payload = _load_issue_working_set()
    frozen_records = [
        record
        for record in payload.get("papers", [])
        if isinstance(record, dict)
        and record.get("state") == "complete"
        and record.get("identity")
        and record.get("key_figure_path")
        and Path(record["key_figure_path"]).is_file()
    ]
    if not frozen_records or not selected:
        return selected
    by_identity = {_selection_id(item.paper): item for item in candidates}
    for record in frozen_records:
        identity = record["identity"]
        snapshot = record.get("scored_paper")
        if identity in by_identity or not isinstance(snapshot, dict):
            continue
        try:
            by_identity[identity] = deserialize_scored_paper(snapshot)
        except (TypeError, ValueError):
            continue
    result = list(selected)
    frozen_ids = {record["identity"] for record in frozen_records}
    present_ids = {_selection_id(item.paper) for item in result}
    for record in frozen_records:
        identity = record["identity"]
        if identity in present_ids or identity not in by_identity:
            continue
        frozen = by_identity[identity]
        bucket = record.get("bucket") or _paper_bucket(frozen.paper, profile)
        replacement_index = next(
            (
                index
                for index in range(len(result) - 1, -1, -1)
                if _paper_bucket(result[index].paper, profile) == bucket
                and _selection_id(result[index].paper) not in frozen_ids
            ),
            None,
        )
        if replacement_index is None:
            continue
        present_ids.discard(_selection_id(result[replacement_index].paper))
        result[replacement_index] = frozen
        present_ids.add(identity)
    return result


def restore_frozen_issue_assets(selected: List[ScoredPaper]) -> None:
    records = {
        record.get("identity"): record
        for record in _load_issue_working_set().get("papers", [])
        if isinstance(record, dict) and record.get("state") == "complete"
    }
    for item in selected:
        record = records.get(_selection_id(item.paper))
        if not record:
            continue
        figure_path = record.get("key_figure_path", "")
        if figure_path and Path(figure_path).is_file():
            item.paper.key_figure_path = figure_path
            item.paper.key_figure_caption = record.get("key_figure_caption", "")
        snapshot = record.get("scored_paper")
        if isinstance(snapshot, dict):
            try:
                frozen = deserialize_scored_paper(snapshot)
            except (TypeError, ValueError):
                continue
            if frozen.paper.fulltext:
                item.paper.fulltext = frozen.paper.fulltext
            if not item.paper.pdf_url and frozen.paper.pdf_url:
                item.paper.pdf_url = frozen.paper.pdf_url
            if isinstance(frozen.paper.raw, dict):
                item.paper.raw = {**frozen.paper.raw, **(item.paper.raw or {})}


def write_issue_working_set(
    provisional: List[ScoredPaper],
    completed: List[ScoredPaper],
    profile,
) -> None:
    complete_ids = {_selection_id(item.paper) for item in completed}
    path = _issue_working_set_path()
    existing = read_json(path, {})
    stages = existing.get("stages") if isinstance(existing, dict) and isinstance(existing.get("stages"), dict) else {}
    payload = {
        "schema_version": 2,
        "issue_date": current_run_date().isoformat(),
        "target_count": profile["selection"]["target_count"],
        "updated_at": utc_now(),
        "stages": stages,
        "papers": [
            {
                "identity": _selection_id(item.paper),
                "doi": item.paper.doi or "",
                "source": item.paper.source,
                "source_id": item.paper.source_id,
                "title": item.paper.title,
                "venue": item.paper.venue or "",
                "bucket": _paper_bucket(item.paper, profile),
                "state": "complete" if _selection_id(item.paper) in complete_ids else "incomplete",
                "key_figure_path": item.paper.key_figure_path or "",
                "key_figure_caption": item.paper.key_figure_caption or "",
                "scored_paper": serialize_scored_paper(item, include_fulltext=True),
            }
            for item in provisional
        ],
    }
    atomic_write_json(path, payload)


def _has_sufficient_figure_coverage(
    with_figures: List[ScoredPaper],
    profile,
    target_count: int,
) -> bool:
    if len(with_figures) < target_count:
        return False
    selected = select_digest(
        with_figures,
        target_count=target_count,
        direct_min=profile["selection"]["direct_min"],
        direct_max=profile["selection"]["direct_max"],
        official_min=profile["selection"]["official_min"],
        preprint_max=profile["selection"].get("preprint_max"),
        tier1_broad_venues=profile.get("tier1_broad_venues"),
        tier2_venues=profile.get("preferred_venues"),
        tier1_target=profile["selection"].get("tier1_target"),
        tier2_min=profile["selection"].get("tier2_min", 0),
        tier2_max=profile["selection"].get("tier2_max"),
        preprint_min=profile["selection"].get("preprint_min", 0),
        max_transferable=profile["selection"].get("max_transferable"),
    )
    if len(selected) < target_count:
        return False
    official_count = sum(1 for item in selected if is_official_source(item.paper.source))
    tier1_venues = profile.get("tier1_broad_venues", []) or []
    tier2_venues = profile.get("preferred_venues", []) or []
    tier2_count = _count_bucket(
        selected,
        "tier2",
        tier1_venues,
        tier2_venues,
    )
    preprint_count = _count_bucket(
        selected,
        "preprint",
        tier1_venues,
        tier2_venues,
    )
    return (
        official_count >= profile["selection"]["official_min"]
        and tier2_count >= profile["selection"].get("tier2_min", 0)
        and preprint_count >= profile["selection"].get("preprint_min", 0)
    )


def _materialize_key_figure_with_timeout(item: ScoredPaper) -> None:
    timeout_seconds = _figure_timeout_seconds()
    if timeout_seconds <= 0 or os.name != "posix":
        materialize_key_figures([item], paper_pk)
        return

    def _handle_timeout(signum, frame):
        raise TimeoutError

    previous_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    try:
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        materialize_key_figures([item], paper_pk)
    except TimeoutError:
        item.paper.key_figure_path = ""
        item.paper.key_figure_caption = ""
        _queue_formal_figure_timeout(item)
        print(
            f"[figures:timeout] seconds={timeout_seconds:g} title={item.paper.title[:120]}",
            file=sys.stderr,
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _queue_formal_figure_timeout(item: ScoredPaper) -> None:
    """Preserve the formal-paper recovery order after a figure timeout.

    A selected Tier 1/2 paper may already have cached full text, so its
    publisher PDF/Figure 1 attempt can time out before ``fetch_pdf_bytes``
    reaches ScanSci's normal MyLOFT queue.  That must not strand the frozen
    same-Tier slot: record the next permitted direct-publisher recovery step.
    ``enqueue_candidate`` is idempotent and refuses records whose current
    direct attempt has already finished.
    """

    paper = item.paper
    metadata = paper.raw.get("paper_radar_recovery", {}) if isinstance(paper.raw, dict) else {}
    if paper.source == "arxiv" or not paper.doi or metadata.get("tier") not in {"tier1", "tier2"}:
        return
    enqueue_candidate(
        paper,
        paper.doi,
        "official PDF or Figure 1 retrieval timed out; await one direct MyLOFT publisher attempt before public-manuscript recovery",
    )


def _figure_timeout_seconds() -> float:
    raw = (os.getenv("PAPER_RADAR_FIGURE_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        scansci_enabled = (os.getenv("PAPER_RADAR_SCANSCI_ENABLED") or "1").strip().lower()
        return 75.0 if scansci_enabled not in {"0", "false", "no", "off"} else 15.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 15.0


def build_figure_candidate_pool(scored_papers: List[ScoredPaper], profile) -> List[ScoredPaper]:
    """Build a source-aware figure extraction pool.

    Total score is still the ranking signal, but arXiv papers should not consume
    most figure extraction slots when the digest later caps them at three items.
    Formal papers get their own large pool so the key-figure gate can actually
    satisfy the official-source minimum.
    """

    selection = profile["selection"]
    target_count = selection["target_count"]
    overall_limit = selection.get("figure_candidate_pool", max(target_count + 6, target_count))
    official_limit = selection.get("official_figure_candidate_pool", max(overall_limit, target_count * 12))
    preprint_limit = selection.get(
        "preprint_figure_candidate_pool",
        max(target_count, (selection.get("preprint_max") or target_count) * 8),
    )

    ranked = rank_candidates(scored_papers)
    pools = [
        ranked[:overall_limit],
        [item for item in ranked if is_official_source(item.paper.source)][:official_limit],
        [item for item in ranked if item.paper.source == "arxiv"][:preprint_limit],
    ]

    candidate_pool: List[ScoredPaper] = []
    seen = set()
    for pool in pools:
        for item in pool:
            key = (item.paper.source, item.paper.source_id, item.paper.doi or "", item.paper.title.lower())
            if key in seen:
                continue
            seen.add(key)
            candidate_pool.append(item)
    return candidate_pool
