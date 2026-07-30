import json
from datetime import date
from pathlib import Path
from typing import Dict, Iterable

from .models import Paper, PaperAnalysis, ScoreBreakdown, ScoredPaper


def export_research_packet(scored_papers: Iterable[ScoredPaper], paper_id_fn, output_dir: str = "output") -> None:
    items = []
    for item in scored_papers:
        paper = item.paper
        items.append(
            {
                "paper_id": paper_id_fn(paper),
                "title": paper.title,
                "authors": paper.authors,
                "venue": paper.venue,
                "published_at": paper.published_at.isoformat(),
                "source": paper.source,
                "doi": paper.doi,
                "url": paper.url,
                "pdf_url": paper.pdf_url,
                "robot_type_tags": paper.robot_type_tags,
                "paper_type": paper.paper_type,
                "score": item.score.total_score,
                "abstract": paper.abstract,
                "fulltext": paper.fulltext,
                "key_figure_path": paper.key_figure_path,
                "key_figure_caption": paper.key_figure_caption,
            }
        )
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "research_packet.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_analyses(path: str) -> Dict[str, PaperAnalysis]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        paper_id: PaperAnalysis(**analysis)
        for paper_id, analysis in payload.items()
        if not paper_id.startswith("_")
    }


def load_issue_editorial(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    editorial = payload.get("_issue_editorial") or payload.get("issue_editorial") or {}
    return editorial if isinstance(editorial, dict) else {}


def load_research_packet(path: str = "output/research_packet.json"):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    items = []
    for item in payload:
        paper = Paper(
            source=item["source"],
            source_id=item["paper_id"].split(":", 1)[1],
            title=item["title"],
            abstract=item["abstract"],
            authors=item["authors"],
            published_at=date.fromisoformat(item["published_at"]),
            venue=item["venue"],
            doi=item.get("doi") or (item["paper_id"].split(":", 1)[1] if item["source"] == "crossref" else None),
            url=item["url"],
            pdf_url=item["pdf_url"],
            robot_type_tags=item["robot_type_tags"],
            paper_type=item["paper_type"],
            fulltext=item["fulltext"],
        )
        paper.key_figure_path = item.get("key_figure_path", "")
        paper.key_figure_caption = item.get("key_figure_caption", "")
        score_value = item["score"]
        score = ScoreBreakdown(score_value, score_value, score_value, score_value, score_value, score_value)
        items.append(ScoredPaper(paper=paper, score=score))
    return items
