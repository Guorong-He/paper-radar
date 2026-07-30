from __future__ import annotations

import gzip
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Paper, ScoreBreakdown, ScoredPaper


SCHEMA_VERSION = 2


def state_root() -> Path:
    return Path(os.getenv("PAPER_RADAR_STATE_ROOT") or "data/issues")


def issue_directory(issue_date: date | str) -> Path:
    value = issue_date.isoformat() if isinstance(issue_date, date) else str(issue_date)
    return state_root() / value


def issue_state_path(issue_date: date | str) -> Path:
    override = os.getenv("PAPER_RADAR_WORKING_SET_PATH")
    return Path(override) if override else issue_directory(issue_date) / "state.json"


def candidate_cache_path(issue_date: date | str) -> Path:
    override = os.getenv("PAPER_RADAR_CANDIDATE_CACHE_PATH")
    return Path(override) if override else issue_directory(issue_date) / "candidates.json.gz"


def run_ledger_path(issue_date: date | str) -> Path:
    override = os.getenv("PAPER_RADAR_RUN_LEDGER_PATH")
    return Path(override) if override else issue_directory(issue_date) / "run-events.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def serialize_paper(
    paper: Paper,
    *,
    include_fulltext: bool = True,
    compact_raw: bool = False,
) -> dict[str, Any]:
    payload = {
        "source": paper.source,
        "source_id": paper.source_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": list(paper.authors),
        "published_at": paper.published_at.isoformat(),
        "venue": paper.venue,
        "doi": paper.doi,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "robot_type_tags": list(paper.robot_type_tags),
        "paper_type": paper.paper_type,
        "signal_groups": list(paper.signal_groups),
        "key_figure_path": paper.key_figure_path,
        "key_figure_caption": paper.key_figure_caption,
        "raw": _compact_candidate_raw(paper.raw) if compact_raw else paper.raw,
    }
    if include_fulltext:
        payload["fulltext"] = paper.fulltext
    return payload


def deserialize_paper(payload: dict[str, Any]) -> Paper:
    return Paper(
        source=str(payload.get("source") or ""),
        source_id=str(payload.get("source_id") or ""),
        title=str(payload.get("title") or ""),
        abstract=str(payload.get("abstract") or ""),
        authors=[str(value) for value in payload.get("authors") or []],
        published_at=date.fromisoformat(str(payload.get("published_at"))),
        venue=str(payload.get("venue") or ""),
        doi=payload.get("doi") or None,
        url=str(payload.get("url") or ""),
        pdf_url=str(payload.get("pdf_url") or ""),
        robot_type_tags=[str(value) for value in payload.get("robot_type_tags") or []],
        paper_type=str(payload.get("paper_type") or "transferable"),
        signal_groups=[str(value) for value in payload.get("signal_groups") or []],
        fulltext=str(payload.get("fulltext") or ""),
        key_figure_path=str(payload.get("key_figure_path") or ""),
        key_figure_caption=str(payload.get("key_figure_caption") or ""),
        raw=payload.get("raw") if isinstance(payload.get("raw"), dict) else {},
    )


def serialize_scored_paper(item: ScoredPaper, *, include_fulltext: bool = True) -> dict[str, Any]:
    return {
        "paper": serialize_paper(item.paper, include_fulltext=include_fulltext),
        "score": {
            "venue_author_score": item.score.venue_author_score,
            "relevance_score": item.score.relevance_score,
            "evidence_score": item.score.evidence_score,
            "freshness_score": item.score.freshness_score,
            "diversity_score": item.score.diversity_score,
            "total_score": item.score.total_score,
        },
    }


def deserialize_scored_paper(payload: dict[str, Any]) -> ScoredPaper:
    score_payload = payload.get("score") if isinstance(payload.get("score"), dict) else {}
    score = ScoreBreakdown(
        float(score_payload.get("venue_author_score") or 0.0),
        float(score_payload.get("relevance_score") or 0.0),
        float(score_payload.get("evidence_score") or 0.0),
        float(score_payload.get("freshness_score") or 0.0),
        float(score_payload.get("diversity_score") or 0.0),
        float(score_payload.get("total_score") or 0.0),
    )
    paper_payload = payload.get("paper") if isinstance(payload.get("paper"), dict) else {}
    return ScoredPaper(deserialize_paper(paper_payload), score)


def save_candidate_cache(
    issue_date: date,
    papers: Iterable[Paper],
    source_status: dict[str, Any],
) -> Path:
    target = candidate_cache_path(issue_date)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "issue_date": issue_date.isoformat(),
        "generated_at": utc_now(),
        "source_status": source_status,
        "papers": [
            serialize_paper(paper, include_fulltext=False, compact_raw=True)
            for paper in papers
        ],
    }
    with gzip.open(temporary, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    os.replace(temporary, target)
    return target


def load_candidate_cache(issue_date: date, path: str | Path | None = None) -> tuple[list[Paper], dict] | None:
    target = Path(path) if path else candidate_cache_path(issue_date)
    try:
        with gzip.open(target, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, EOFError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        return None
    try:
        papers = [deserialize_paper(item) for item in payload["papers"] if isinstance(item, dict)]
    except (TypeError, ValueError):
        return None
    metadata = {
        "issue_date": payload.get("issue_date"),
        "generated_at": payload.get("generated_at"),
        "source_status": payload.get("source_status") if isinstance(payload.get("source_status"), dict) else {},
        "path": str(target),
    }
    return papers, metadata


def load_latest_candidate_catalog(before: date) -> tuple[list[Paper], dict] | None:
    candidates: list[tuple[date, Path]] = []
    root = state_root()
    if not root.is_dir():
        return None
    for path in root.glob("*/candidates.json.gz"):
        try:
            issue_date = date.fromisoformat(path.parent.name)
        except ValueError:
            continue
        if issue_date < before:
            candidates.append((issue_date, path))
    for issue_date, path in sorted(candidates, reverse=True):
        loaded = load_candidate_cache(issue_date, path=path)
        if loaded:
            return loaded
    return None


def begin_run(issue_date: date, *, fixture: bool, analyze: bool) -> str:
    run_id = f"{issue_date.isoformat()}-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    record_run_event(
        issue_date,
        run_id,
        "run",
        "started",
        {"fixture": fixture, "analyze": analyze},
    )
    mark_stage(issue_date, "run", "in_progress", {"run_id": run_id})
    return run_id


def record_run_event(
    issue_date: date,
    run_id: str,
    stage: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    path = run_ledger_path(issue_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": utc_now(),
        "run_id": run_id,
        "stage": stage,
        "status": status,
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def mark_stage(
    issue_date: date,
    stage: str,
    status: str,
    details: dict[str, Any] | None = None,
) -> None:
    path = issue_state_path(issue_date)
    payload = read_json(path, {})
    if not isinstance(payload, dict) or payload.get("issue_date") not in {None, issue_date.isoformat()}:
        payload = {}
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    stages[stage] = {
        "status": status,
        "updated_at": utc_now(),
        "details": details or {},
    }
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "issue_date": issue_date.isoformat(),
            "updated_at": utc_now(),
            "stages": stages,
        }
    )
    atomic_write_json(path, payload)


def compact_run_report(issue_date: date) -> dict[str, Any]:
    state = read_json(issue_state_path(issue_date), {})
    events = []
    try:
        lines = run_ledger_path(issue_date).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines[-30:]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    papers = state.get("papers") if isinstance(state, dict) and isinstance(state.get("papers"), list) else []
    return {
        "issue_date": issue_date.isoformat(),
        "target_count": state.get("target_count", 0) if isinstance(state, dict) else 0,
        "completed_count": sum(record.get("state") == "complete" for record in papers if isinstance(record, dict)),
        "incomplete_count": sum(record.get("state") != "complete" for record in papers if isinstance(record, dict)),
        "stages": state.get("stages", {}) if isinstance(state, dict) else {},
        "recent_events": events,
    }


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _compact_candidate_raw(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    article_type_keys = {"article_type", "article-type", "subtype", "type"}
    return {
        key: value
        for key, value in raw.items()
        if key in article_type_keys or key.startswith("paper_radar_")
    }
