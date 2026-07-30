#!/usr/bin/env python3
"""Build a manually curated packet with strict full-text and figure gates."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from paper_radar.config import load_profile
from paper_radar.db import connect, paper_pk, upsert_papers, upsert_scores
from paper_radar.figures import materialize_key_figures
from paper_radar.fulltext import extract_abstract_from_fulltext, fetch_fulltext
from paper_radar.models import Paper, ScoreBreakdown, ScoredPaper
from paper_radar.packet import export_research_packet
from paper_radar.pipeline import current_run_date
from paper_radar.scoring import score_papers
from paper_radar.tagging import enrich_papers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/paper_radar.db")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--paper-id", action="append", required=True)
    return parser.parse_args()


def load_scored_paper(db_path: str, paper_id: str) -> ScoredPaper:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT p.*, s.venue_author_score, s.relevance_score, s.evidence_score,
                   s.freshness_score, s.diversity_score, s.total_score
            FROM papers p
            LEFT JOIN paper_scores s ON s.paper_id = p.id
            WHERE p.id = ?
            """,
            (paper_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Unknown paper id: {paper_id}")
    paper = Paper(
        source=row["source"],
        source_id=row["source_id"],
        doi=row["doi"] or None,
        title=row["title"],
        abstract=row["abstract"],
        venue=row["venue"] or "",
        authors=json.loads(row["authors_json"]),
        published_at=date.fromisoformat(row["published_at"]),
        url=row["url"] or "",
        pdf_url=row["pdf_url"] or "",
        robot_type_tags=json.loads(row["robot_type_tags_json"]),
        paper_type=row["paper_type"],
        signal_groups=json.loads(row["signal_groups_json"]),
        fulltext=row["fulltext"] or "",
        raw=json.loads(row["raw_json"]),
    )
    score_fields = (
        "venue_author_score",
        "relevance_score",
        "evidence_score",
        "freshness_score",
        "diversity_score",
        "total_score",
    )
    score_values = [row[field] for field in score_fields]
    if any(value is None for value in score_values):
        score_values = [0.0] * len(score_fields)
    return ScoredPaper(paper=paper, score=ScoreBreakdown(*map(float, score_values)))


def main() -> None:
    args = parse_args()
    items = [load_scored_paper(args.db, paper_id) for paper_id in args.paper_id]
    for item in items:
        if not item.paper.fulltext.strip():
            item.paper.fulltext = fetch_fulltext(item.paper) or ""
        if not item.paper.abstract.strip():
            item.paper.abstract = extract_abstract_from_fulltext(item.paper.fulltext)
        print(f"[curate:fulltext] {paper_pk(item.paper)} {len(item.paper.fulltext)}", flush=True)

    profile = load_profile()
    items = score_papers(
        enrich_papers([item.paper for item in items], profile),
        profile,
        current_run_date(),
    )

    figure_dir = Path(args.output_dir) / "figures"
    materialize_key_figures(items, paper_pk, output_dir=str(figure_dir))
    missing_fulltext = [paper_pk(item.paper) for item in items if not item.paper.fulltext.strip()]
    missing_figures = [
        paper_pk(item.paper)
        for item in items
        if not item.paper.key_figure_path or not Path(item.paper.key_figure_path).is_file()
    ]
    if missing_fulltext or missing_figures:
        raise RuntimeError(
            f"Strict curation gate failed; missing full text={missing_fulltext}, "
            f"missing figures={missing_figures}"
        )

    upsert_papers(args.db, [item.paper for item in items])
    upsert_scores(args.db, items)
    export_research_packet(items, paper_pk, output_dir=args.output_dir)
    print(f"[curate:complete] wrote {len(items)} papers", flush=True)


if __name__ == "__main__":
    main()
