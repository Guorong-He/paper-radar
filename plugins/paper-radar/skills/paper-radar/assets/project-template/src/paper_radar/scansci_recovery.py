import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from .models import Paper
from .myloft import (
    enqueue_candidate,
    has_exhausted_recovery_paths,
    has_terminal_recovery_failure,
    mark_public_recovery_terminal,
    record_public_recovery_timeout,
)


DEFAULT_SCANSCI_PYTHON = Path.home() / ".codex" / "tools" / "scansci-pdf" / "bin" / "python"
DEFAULT_SCANSCI_DATA_DIR = Path.home() / ".codex" / "paper-radar-scansci"
APPROVED_SOURCES = {
    "NatureDirect",
    "ElsevierAPI",
    "Unpaywall",
    "OpenAlexOA",
    "OpenAlexContent",
    "AuthorArXiv",
    "AuthorPublicManuscript",
    "InstitutionalRepository",
    "SemanticScholar",
    "DOAJ",
    "EuropePMC",
    "PMC",
    "CORE",
    "CrossrefPage",
    "InstSci",
    "CARSI",
    "WebVPN",
    "EZProxy",
    "MyLOFT",
    "LocalDownload",
}


def recover_pdf_bytes(
    paper: Paper,
    output_dir: str | Path = "output/recovered_pdfs",
) -> Optional[bytes]:
    """Recover a formal paper through ScanSci PDF's approved-source subset.

    This is deliberately not the ScanSci smart downloader: Paper Radar never
    enables Sci-Hub, LibGen, Tor, CAPTCHA solving, or anti-detection sources.
    """

    if paper.source == "arxiv" or not _recovery_enabled():
        return None
    doi = canonical_doi(paper)
    if not doi:
        _record_audit(paper, "", "skipped_no_doi")
        return None

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", doi)
    pdf_path = output_path / f"{stem}.pdf"
    provenance_path = output_path / f"{stem}.json"

    cached = _validated_cached_pdf(pdf_path, provenance_path, paper)
    if cached:
        _record_audit(paper, doi, "cache_hit", source=_provenance_source(provenance_path))
        return cached

    if has_exhausted_recovery_paths(doi):
        _record_audit(paper, doi, "recovery_paths_exhausted")
        return None

    # Formal papers retain their original Tier when a lawful public version is
    # used, but the user wants the authorized publisher route attempted first.
    # A current MyLOFT failure is therefore the evidence that releases the
    # automatic third recovery layer below.
    if _awaiting_current_myloft_attempt(paper, doi):
        return None

    python_path = Path(os.getenv("PAPER_RADAR_SCANSCI_PYTHON") or DEFAULT_SCANSCI_PYTHON)
    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "scansci_recover.py"
    if not python_path.is_file() or not runner_path.is_file():
        _record_audit(paper, doi, "runtime_unavailable")
        _enqueue_if_not_terminal(paper, doi, "approved public-manuscript recovery runtime unavailable")
        return None

    env = os.environ.copy()
    env.setdefault("SCANSCI_PDF_DATA_DIR", str(DEFAULT_SCANSCI_DATA_DIR))
    timeout = _recovery_timeout_seconds()
    try:
        completed = subprocess.run(
            [
                str(python_path),
                str(runner_path),
                doi,
                "--output",
                str(pdf_path),
                "--title",
                paper.title,
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _record_audit(paper, doi, "timeout")
        record_public_recovery_timeout(doi)
        _enqueue_if_not_terminal(paper, doi, "approved public-manuscript recovery timed out")
        return None
    except OSError as exc:
        _record_audit(paper, doi, "runner_error", detail=type(exc).__name__)
        _enqueue_if_not_terminal(paper, doi, "approved public-manuscript recovery runner error")
        return None

    result = _parse_runner_result(completed.stdout)
    if completed.returncode != 0 or not result.get("success"):
        _record_audit(paper, doi, "not_recovered", attempts=result.get("attempts", []))
        mark_public_recovery_terminal(doi, "no approved public or configured institutional PDF source succeeded")
        _enqueue_if_not_terminal(paper, doi, "no approved public or configured institutional PDF source succeeded")
        return None
    if result.get("source") not in APPROVED_SOURCES:
        pdf_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        _record_audit(paper, doi, "rejected_source", source=result.get("source", ""))
        mark_public_recovery_terminal(doi, "recovery source provenance was not approved")
        _enqueue_if_not_terminal(paper, doi, "recovery source provenance was not approved")
        return None
    payload = _validated_cached_pdf(pdf_path, provenance_path, paper)
    if not payload:
        _record_audit(paper, doi, "rejected_validation", source=result.get("source", ""))
        mark_public_recovery_terminal(doi, "recovered file failed PDF or identity validation")
        _enqueue_if_not_terminal(paper, doi, "recovered file failed PDF or identity validation")
        return None
    _record_audit(paper, doi, "recovered", source=result.get("source", ""))
    return payload


def reset_recovery_audit() -> None:
    # Recovery evidence is append-only within an issue. Earlier retries are
    # needed to diagnose repeated timeouts and must not disappear at rerun.
    _audit_path().parent.mkdir(parents=True, exist_ok=True)


def canonical_doi(paper: Paper) -> str:
    value = paper.doi or (paper.source_id if paper.source == "crossref" else "")
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned)
    cleaned = re.sub(r"^doi:\s*", "", cleaned)
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", cleaned)
    return match.group(0).rstrip(".,;)") if match else ""


def _awaiting_current_myloft_attempt(paper: Paper, doi: str) -> bool:
    """Queue a formal paper until one current authorized publisher attempt ends."""

    if not _is_formal_tier_candidate(paper) or has_terminal_recovery_failure(doi):
        return False
    queued = enqueue_candidate(
        paper,
        doi,
        "official PDF unavailable; await one direct MyLOFT publisher attempt before public-manuscript recovery",
    )
    if queued:
        _record_audit(paper, doi, "awaiting_myloft_direct_attempt")
    return queued


def _enqueue_if_not_terminal(paper: Paper, doi: str, reason: str) -> None:
    if not has_terminal_recovery_failure(doi):
        enqueue_candidate(paper, doi, reason)


def _is_formal_tier_candidate(paper: Paper) -> bool:
    metadata = paper.raw.get("paper_radar_recovery", {}) if isinstance(paper.raw, dict) else {}
    return paper.source != "arxiv" and metadata.get("tier") in {"tier1", "tier2"}


def _validated_cached_pdf(pdf_path: Path, provenance_path: Path, paper: Paper) -> Optional[bytes]:
    if not pdf_path.is_file() or not provenance_path.is_file():
        return None
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if provenance.get("source") not in APPROVED_SOURCES:
            return None
        payload = pdf_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None
    if not _valid_recovered_pdf(payload, paper.title):
        pdf_path.unlink(missing_ok=True)
        provenance_path.unlink(missing_ok=True)
        return None
    return payload


def _valid_recovered_pdf(payload: bytes, expected_title: str) -> bool:
    if len(payload) < 10_000 or not payload.lstrip().startswith(b"%PDF"):
        return False
    try:
        reader = PdfReader(BytesIO(payload))
        if len(reader.pages) < 2:
            return False
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception:
        return False
    if len(text.strip()) < 500:
        return False
    header = re.sub(r"\s+", " ", text[:1500].lower())
    if "supplementary information" in header or "supporting information" in header:
        return False
    title_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", (expected_title or "").lower())
        if len(token) >= 4
    ]
    if not title_tokens:
        return True
    text_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    coverage = sum(token in text_tokens for token in set(title_tokens)) / len(set(title_tokens))
    return coverage >= 0.5


def _parse_runner_result(stdout: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        if not line.startswith("SCANSCI_RESULT="):
            continue
        try:
            payload = json.loads(line.split("=", 1)[1])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _provenance_source(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("source") or "")


def _record_audit(paper: Paper, doi: str, status: str, **details) -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source_id": paper.source_id,
        "doi": doi,
        "title": paper.title,
        "venue": paper.venue,
        "status": status,
        **details,
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _audit_path() -> Path:
    override = os.getenv("PAPER_RADAR_SCANSCI_AUDIT_PATH")
    if override:
        return Path(override)
    issue_date = os.getenv("PAPER_RADAR_RUN_DATE") or os.getenv("PAPER_RADAR_ISSUE_DATE") or date.today().isoformat()
    root = Path(os.getenv("PAPER_RADAR_STATE_ROOT") or "data/issues")
    return root / issue_date / "recovery-events.jsonl"


def _recovery_enabled() -> bool:
    raw = (os.getenv("PAPER_RADAR_SCANSCI_ENABLED") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _recovery_timeout_seconds() -> float:
    raw = (os.getenv("PAPER_RADAR_SCANSCI_TIMEOUT_SECONDS") or "45").strip()
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 45.0
