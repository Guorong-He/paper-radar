import json
from pathlib import Path
from typing import Iterable

from .models import PaperAnalysis, ScoredPaper


def export_digest(
    scored_papers: Iterable[ScoredPaper],
    analyses_by_paper_id,
    paper_id_fn,
    output_dir: str = "output",
) -> None:
    items = list(scored_papers)
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)

    payload = []
    for item in items:
        analysis: PaperAnalysis = analyses_by_paper_id[paper_id_fn(item.paper)]
        payload.append(
            {
                "title": item.paper.title,
                "authors": item.paper.authors,
                "venue": item.paper.venue,
                "published_at": item.paper.published_at.isoformat(),
                "paper_type": item.paper.paper_type,
                "robot_type_tags": item.paper.robot_type_tags,
                "score": item.score.total_score,
                "url": item.paper.url,
                "pdf_url": item.paper.pdf_url,
                "analysis": {
                    "core_insight": analysis.core_insight,
                    "problem_frame": analysis.problem_frame,
                    "first_principles": analysis.first_principles,
                    "mechanism": analysis.mechanism,
                    "boundary_advanced": analysis.boundary_advanced,
                    "old_problem": analysis.old_problem,
                    "why_it_works": analysis.why_it_works,
                    "true_novelty": analysis.true_novelty,
                    "evidence_summary": analysis.evidence_summary,
                    "email_summary": analysis.email_summary,
                    "importance_reason": analysis.importance_reason,
                },
            }
        )

    (path / "digest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    markdown_lines = ["# Paper Radar Digest", ""]
    for idx, item in enumerate(items, start=1):
        analysis: PaperAnalysis = analyses_by_paper_id[paper_id_fn(item.paper)]
        markdown_lines.extend(
            [
                f"## {idx}. {item.paper.title}",
                f"- Venue: {item.paper.venue or 'Unknown'}",
                f"- Published: {item.paper.published_at.isoformat()}",
                f"- Type: {item.paper.paper_type}",
                f"- Tags: {', '.join(item.paper.robot_type_tags) or 'none'}",
                f"- Score: {item.score.total_score}",
                f"- Core insight: {analysis.core_insight}",
                f"- Problem frame: {analysis.problem_frame}",
                f"- First principles: {analysis.first_principles}",
                f"- Mechanism: {analysis.mechanism}",
                f"- Boundary advanced: {analysis.boundary_advanced}",
                f"- Old problem: {analysis.old_problem}",
                f"- Why it works: {analysis.why_it_works}",
                f"- True novelty: {analysis.true_novelty}",
                f"- Evidence: {analysis.evidence_summary}",
                "",
            ]
        )
    (path / "digest.md").write_text("\n".join(markdown_lines), encoding="utf-8")


def export_analyses_json(analyses_by_paper_id, output_dir: str = "output") -> None:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        paper_id: analysis.__dict__
        for paper_id, analysis in analyses_by_paper_id.items()
    }
    (path / "analyses.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
