import json
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader

from .models import ScoredPaper
from .myloft import import_discovered_download, validate_pdf_identity
from .state import atomic_write_json


def ingest_recent_downloads(
    scored_papers: list[ScoredPaper],
    downloads_dir: str | Path | None = None,
    audit_path: str | Path = "output/local_download_intake.json",
    scan_label: str = "intake",
) -> list[dict]:
    """Import and prioritize recent PDFs that match strict current candidates.

    The download folder is an intake surface, not a relevance bypass.  This
    function sees only papers that already passed the normal metadata scope
    filter and score floor.  A validated local PDF then receives priority
    inside its existing Tier and can revive a prematurely skipped recovery.
    """

    root = Path(downloads_dir or os.getenv("PAPER_RADAR_DOWNLOADS_DIR") or Path.home() / "Downloads")
    records: list[dict] = []
    if not root.is_dir() or not scored_papers:
        _write_audit(audit_path, records)
        return records

    cutoff = datetime.now(timezone.utc) - timedelta(hours=_lookback_hours())
    candidates = list(scored_papers)
    matched_papers: set[str] = set()
    for pdf_path in _recent_pdfs(root, cutoff):
        try:
            payload = pdf_path.read_bytes()
            identity_text = _pdf_identity_text(payload)
        except OSError:
            continue
        if not identity_text:
            continue
        match = _match_candidate(identity_text, candidates)
        if match is None:
            continue
        paper = match.paper
        identity = (paper.doi or f"{paper.source}:{paper.source_id}").casefold()
        if identity in matched_papers:
            continue
        ok, detail = validate_pdf_identity(
            payload,
            paper.title,
            paper.doi or "",
            allow_strong_title_without_doi=True,
        )
        if not ok:
            continue
        target = import_discovered_download(paper, pdf_path)
        paper.raw = dict(paper.raw or {})
        paper.raw["paper_radar_local_download"] = {
            "priority": True,
            "source_file": pdf_path.name,
            "local_pdf": str(target),
        }
        records.append(
            {
                "paper_id": paper.doi or f"{paper.source}:{paper.source_id}",
                "title": paper.title,
                "source_file": pdf_path.name,
                "local_pdf": str(target),
                "validation": detail,
                "scan_label": scan_label,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        matched_papers.add(identity)
    _write_audit(audit_path, records)
    return records


def _recent_pdfs(root: Path, cutoff: datetime) -> list[Path]:
    paths = []
    for path in root.glob("*.pdf"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if modified >= cutoff:
            paths.append(path)
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)[: _max_files()]


def _pdf_identity_text(payload: bytes) -> str:
    if len(payload) < 10_000 or not payload.lstrip().startswith(b"%PDF"):
        return ""
    try:
        reader = PdfReader(BytesIO(payload))
        metadata = " ".join(
            str(value or "")
            for value in (
                getattr(reader.metadata, "title", ""),
                getattr(reader.metadata, "subject", ""),
            )
        )
        pages = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception:
        return ""
    return f"{metadata}\n{pages}" if len(pages.strip()) >= 300 else ""


def _match_candidate(identity_text: str, candidates: Iterable[ScoredPaper]) -> ScoredPaper | None:
    compact = re.sub(r"[^a-z0-9]+", "", identity_text.casefold())
    text_tokens = set(re.findall(r"[a-z0-9]+", identity_text.casefold()))
    best = None
    best_coverage = 0.0
    for item in candidates:
        paper = item.paper
        doi = _canonical_doi(paper.doi or "")
        if doi:
            compact_doi = re.sub(r"[^a-z0-9]+", "", doi)
            article_id = re.sub(r"[^a-z0-9]+", "", doi.rsplit("/", 1)[-1].rsplit(".", 1)[-1])
            if compact_doi in compact or (len(article_id) >= 6 and article_id in compact):
                return item
        title_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", paper.title.casefold())
            if len(token) >= 4
        }
        coverage = sum(token in text_tokens for token in title_tokens) / max(1, len(title_tokens))
        if coverage > best_coverage:
            best = item
            best_coverage = coverage
    return best if best_coverage >= 0.75 else None


def _canonical_doi(value: str) -> str:
    cleaned = (value or "").strip().casefold()
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned)
    cleaned = re.sub(r"^doi:\s*", "", cleaned)
    return cleaned


def _lookback_hours() -> int:
    return _positive_int("PAPER_RADAR_DOWNLOAD_LOOKBACK_HOURS", 168)


def _max_files() -> int:
    return _positive_int("PAPER_RADAR_DOWNLOAD_MAX_FILES", 40)


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name) or default))
    except ValueError:
        return default


def _write_audit(path: str | Path, records: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_records = []
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("papers"), list):
            existing_records = [item for item in existing["papers"] if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    merged = {
        (str(item.get("paper_id") or ""), str(item.get("source_file") or "")): item
        for item in existing_records
    }
    for record in records:
        merged[(str(record.get("paper_id") or ""), str(record.get("source_file") or ""))] = record
    atomic_write_json(
        target,
        {
            "matched_count": len(merged),
            "last_scan_match_count": len(records),
            "papers": list(merged.values()),
        },
    )
