import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .models import Paper, PaperAnalysis, ScoredPaper


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    with connect(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))


def paper_pk(paper: Paper) -> str:
    return f"{paper.source}:{paper.source_id}"


def safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).encode("utf-8", errors="replace").decode("utf-8")


def safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def upsert_papers(db_path: str, papers: Iterable[Paper]) -> None:
    now = datetime.utcnow().isoformat()
    with connect(db_path) as conn:
        for paper in papers:
            conn.execute(
                """
                INSERT INTO papers (
                    id, source, source_id, doi, title, abstract, venue, authors_json,
                    published_at, url, pdf_url, robot_type_tags_json, paper_type, signal_groups_json, fulltext, raw_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    doi=excluded.doi,
                    title=excluded.title,
                    abstract=excluded.abstract,
                    venue=excluded.venue,
                    authors_json=excluded.authors_json,
                    published_at=excluded.published_at,
                    url=excluded.url,
                    pdf_url=excluded.pdf_url,
                    robot_type_tags_json=excluded.robot_type_tags_json,
                    paper_type=excluded.paper_type,
                    signal_groups_json=excluded.signal_groups_json,
                    fulltext=excluded.fulltext,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    paper_pk(paper),
                    safe_text(paper.source),
                    safe_text(paper.source_id),
                    safe_text(paper.doi),
                    safe_text(paper.title),
                    safe_text(paper.abstract),
                    safe_text(paper.venue),
                    safe_json([safe_text(author) for author in paper.authors]),
                    paper.published_at.isoformat(),
                    safe_text(paper.url),
                    safe_text(paper.pdf_url),
                    safe_json([safe_text(tag) for tag in paper.robot_type_tags]),
                    safe_text(paper.paper_type),
                    safe_json([safe_text(group) for group in paper.signal_groups]),
                    safe_text(paper.fulltext),
                    safe_json(paper.raw),
                    now,
                    now,
                ),
            )


def load_candidate_papers(
    db_path: str,
    published_from: date,
    published_until: date,
) -> list[Paper]:
    """Load a bounded metadata catalog for cache bootstrapping.

    The database is a fallback catalog, not the authoritative source for the
    current issue. Keep this read-only and exclude full text so warming a
    candidate cache cannot expose or duplicate analysis material.
    """

    path = Path(db_path)
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source, source_id, doi, title, abstract, venue, authors_json,
                   published_at, url, pdf_url, robot_type_tags_json, paper_type,
                   signal_groups_json, raw_json
            FROM papers
            WHERE published_at >= ? AND published_at <= ?
            ORDER BY published_at DESC
            """,
            (published_from.isoformat(), published_until.isoformat()),
        ).fetchall()
    papers = []
    for row in rows:
        try:
            published_at = date.fromisoformat(row["published_at"])
        except (TypeError, ValueError):
            continue
        if not row["source"] or not row["source_id"] or not row["title"]:
            continue
        papers.append(
            Paper(
                source=safe_text(row["source"]),
                source_id=safe_text(row["source_id"]),
                doi=safe_text(row["doi"]) or None,
                title=safe_text(row["title"]),
                abstract=safe_text(row["abstract"]),
                venue=safe_text(row["venue"]),
                authors=_json_list(row["authors_json"]),
                published_at=published_at,
                url=safe_text(row["url"]),
                pdf_url=safe_text(row["pdf_url"]),
                robot_type_tags=_json_list(row["robot_type_tags_json"]),
                paper_type=safe_text(row["paper_type"]) or "transferable",
                signal_groups=_json_list(row["signal_groups_json"]),
                raw=_json_dict(row["raw_json"]),
            )
        )
    return papers


def _json_list(value) -> list[str]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [safe_text(item) for item in payload] if isinstance(payload, list) else []


def _json_dict(value) -> dict:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def upsert_analyses(db_path: str, analyses_by_paper_id) -> None:
    now = datetime.utcnow().isoformat()
    with connect(db_path) as conn:
        for paper_id, analysis in analyses_by_paper_id.items():
            conn.execute(
                """
                INSERT INTO paper_analyses (
                    paper_id, core_insight, problem_frame, first_principles, mechanism,
                    boundary_advanced, old_problem, why_it_works, true_novelty,
                    evidence_summary, email_summary, importance_reason, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    core_insight=excluded.core_insight,
                    problem_frame=excluded.problem_frame,
                    first_principles=excluded.first_principles,
                    mechanism=excluded.mechanism,
                    boundary_advanced=excluded.boundary_advanced,
                    old_problem=excluded.old_problem,
                    why_it_works=excluded.why_it_works,
                    true_novelty=excluded.true_novelty,
                    evidence_summary=excluded.evidence_summary,
                    email_summary=excluded.email_summary,
                    importance_reason=excluded.importance_reason,
                    analyzed_at=excluded.analyzed_at
                """,
                (
                    paper_id,
                    analysis.core_insight,
                    analysis.problem_frame,
                    analysis.first_principles,
                    analysis.mechanism,
                    analysis.boundary_advanced,
                    analysis.old_problem,
                    analysis.why_it_works,
                    analysis.true_novelty,
                    analysis.evidence_summary,
                    analysis.email_summary,
                    analysis.importance_reason,
                    now,
                ),
            )


def upsert_scores(db_path: str, scored_papers: Iterable[ScoredPaper]) -> None:
    now = datetime.utcnow().isoformat()
    with connect(db_path) as conn:
        for scored in scored_papers:
            score = scored.score
            conn.execute(
                """
                INSERT INTO paper_scores (
                    paper_id, venue_author_score, relevance_score, evidence_score,
                    freshness_score, diversity_score, total_score, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET
                    venue_author_score=excluded.venue_author_score,
                    relevance_score=excluded.relevance_score,
                    evidence_score=excluded.evidence_score,
                    freshness_score=excluded.freshness_score,
                    diversity_score=excluded.diversity_score,
                    total_score=excluded.total_score,
                    scored_at=excluded.scored_at
                """,
                (
                    paper_pk(scored.paper),
                    score.venue_author_score,
                    score.relevance_score,
                    score.evidence_score,
                    score.freshness_score,
                    score.diversity_score,
                    score.total_score,
                    now,
                ),
            )
