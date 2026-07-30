import json
import os
import re
import shutil
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

from pypdf import PdfReader

from .models import Paper


DEFAULT_PORTAL_URL = "https://app.myloft.xyz/browse/home"
LEGACY_QUEUE_PATH = Path("output/myloft_download_queue.json")
DEFAULT_LEDGER_PATH = Path("data/myloft_rate_limit.json")
DEFAULT_RECOVERY_DIR = Path("output/recovered_pdfs")


def enqueue_candidate(paper: Paper, doi: str, reason: str) -> bool:
    """Queue one already-screened formal paper for visible MyLOFT recovery.

    This function never opens a browser or downloads anything. The metadata
    queue retains every eligible Tier 1/2 failure, while a separate persistent
    budget limits which entries may actually be downloaded.
    """

    if not _enabled() or paper.source == "arxiv" or not doi:
        return False
    issue_date = _issue_date()
    queue = _load_queue(issue_date)
    papers = queue["papers"]
    normalized = _canonical_doi(doi)
    tier = _paper_tier(paper)
    if tier not in {"tier1", "tier2"}:
        return False
    for item in papers:
        if _canonical_doi(item.get("doi", "")) == normalized:
            if item.get("status") == "pending":
                # A matching DOI may have remained in the metadata-only queue from
                # an earlier run.  Refresh its auditable ranking data from the
                # current strict semantic pass rather than letting stale relevance
                # or venue metadata decide the next visible-browser download.
                _refresh_pending_entry(item, paper, normalized, reason, tier)
                _refresh_queue_priorities(queue, issue_date)
                _write_json(_queue_path(), queue)
                return True
            if item.get("status") == "imported" or item.get("direct_publisher_attempted"):
                return False
            # Historical queue outcomes may come from discovery pages or an
            # earlier session, not a current direct publisher download through
            # the user's authorized MyLOFT Chrome session. Keep that audit
            # history, but let a paper that passes this run's strict metadata
            # selection reach the direct publisher path once.
            item["status"] = "pending"
            item["queued_at"] = _now().isoformat()
            item["previous_skip_reason"] = item.get("skip_reason", "")
            item["previous_skipped_at"] = item.get("skipped_at", "")
            item.pop("direct_publisher_attempted", None)
            _refresh_pending_entry(item, paper, normalized, reason, tier)
            _refresh_queue_priorities(queue, issue_date)
            _write_json(_queue_path(), queue)
            return True

    queued_at = _now()
    metadata = paper.raw.get("paper_radar_recovery", {}) if isinstance(paper.raw, dict) else {}
    papers.append(
        {
            "doi": normalized,
            "title": paper.title,
            "venue": paper.venue,
            "publisher_url": paper.url or f"https://doi.org/{normalized}",
            "portal_url": _portal_url(),
            "reason": reason,
            "status": "pending",
            "tier": tier,
            "total_score": float(metadata.get("total_score") or 0.0),
            "relevance_score": float(metadata.get("relevance_score") or 0.0),
            "published_at": metadata.get("published_at") or paper.published_at.isoformat(),
            "queued_at": queued_at.isoformat(),
        }
    )
    _refresh_queue_priorities(queue, issue_date)
    _write_json(_queue_path(), queue)
    return True


def reconcile_pending_candidates(papers: Iterable[Paper]) -> int:
    """Retire pending queue entries absent from this run's strict candidate set.

    MyLOFT is only a local recovery queue, not a source of discovery.  Keeping
    terminal records is useful for auditability, but permitting an older pending
    entry to survive a stricter semantic filter would reintroduce off-scope
    papers.  This function never touches imported/skipped records or files.
    """

    issue_date = _issue_date()
    approved_dois = {
        _canonical_doi(paper.doi)
        for paper in papers
        if paper.doi and paper.source != "arxiv" and _paper_tier(paper) in {"tier1", "tier2"}
    }
    queue = _load_queue(issue_date)
    reconciled_at = _now().isoformat()
    retired = 0
    for item in queue["papers"]:
        if item.get("status") != "pending":
            continue
        if _canonical_doi(item.get("doi", "")) in approved_dois:
            continue
        item["status"] = "skipped"
        item["skipped_at"] = reconciled_at
        item["skip_reason"] = "absent_from_current_strict_semantic_candidate_set"
        retired += 1
    _refresh_queue_priorities(queue, issue_date)
    _write_json(_queue_path(), queue)
    return retired


def import_download(
    doi: str,
    pdf_path: str | Path,
    recovery_dir: str | Path = DEFAULT_RECOVERY_DIR,
) -> Path:
    """Validate and import one visible-browser MyLOFT download.

    The original download is retained. The validated local copy is stored with
    restrictive permissions and approved-source provenance for the existing
    Paper Radar recovery cache.
    """

    normalized = _canonical_doi(doi)
    issue_date = _issue_date()
    queue = _load_queue(issue_date)
    _refresh_queue_priorities(queue, issue_date)
    item = next(
        (
            entry
            for entry in queue["papers"]
            if _canonical_doi(entry.get("doi", "")) == normalized and entry.get("status") == "pending"
        ),
        None,
    )
    if item is None:
        raise ValueError(f"DOI is not in the current pending MyLOFT queue: {normalized}")
    if not item.get("download_eligible"):
        raise RuntimeError("MyLOFT candidate is outside the current per-issue download budget")
    _enforce_rate_limit(issue_date)

    source = Path(pdf_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    payload = source.read_bytes()
    ok, detail = validate_pdf_identity(payload, item.get("title", ""), normalized)
    if not ok:
        raise ValueError(f"Rejected MyLOFT PDF: {detail}")

    target_dir = Path(recovery_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", normalized)
    target = target_dir / f"{stem}.pdf"
    provenance = target.with_suffix(".json")
    shutil.copy2(source, target)
    record = {
        "doi": normalized,
        "title": item.get("title", ""),
        "source": "MyLOFT",
        "institution": "Tsinghua University",
        "portal_url": _portal_url(),
        "publisher_url": item.get("publisher_url", ""),
        "imported_at": _now().isoformat(),
    }
    provenance.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)
    os.chmod(provenance, 0o600)

    imported_at = _now()
    item["status"] = "imported"
    item["imported_at"] = imported_at.isoformat()
    item["local_pdf"] = str(target)
    _write_json(_queue_path(), queue)
    _record_import(issue_date, normalized, imported_at)
    return target


def import_discovered_download(
    paper: Paper,
    pdf_path: str | Path,
    recovery_dir: str | Path = DEFAULT_RECOVERY_DIR,
) -> Path:
    """Import a newly discovered user/agent download and revive its candidate.

    Unlike ``import_download``, this reconciliation path intentionally accepts
    a queue item that was prematurely skipped before a browser download
    finished.  It does not consume an automated MyLOFT start/import budget;
    those limits are enforced where the automated browser action begins.
    """

    normalized = _canonical_doi(paper.doi or paper.source_id)
    if not normalized:
        raise ValueError("A discovered formal download requires a DOI")
    source = Path(pdf_path).expanduser().resolve()
    payload = source.read_bytes()
    ok, detail = validate_pdf_identity(
        payload,
        paper.title,
        normalized,
        allow_strong_title_without_doi=True,
    )
    if not ok:
        raise ValueError(f"Rejected discovered PDF: {detail}")

    target_dir = Path(recovery_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9_.-]+", "_", normalized)
    target = target_dir / f"{stem}.pdf"
    provenance = target.with_suffix(".json")
    shutil.copy2(source, target)
    imported_at = _now()
    provenance.write_text(
        json.dumps(
            {
                "doi": normalized,
                "title": paper.title,
                "source": "LocalDownload",
                "source_file": source.name,
                "imported_at": imported_at.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.chmod(target, 0o600)
    os.chmod(provenance, 0o600)

    issue_date = _issue_date()
    queue = _load_queue(issue_date)
    item = next(
        (entry for entry in queue["papers"] if _canonical_doi(entry.get("doi", "")) == normalized),
        None,
    )
    if item is None:
        item = {
            "doi": normalized,
            "title": paper.title,
            "venue": paper.venue,
            "publisher_url": paper.url or f"https://doi.org/{normalized}",
            "portal_url": _portal_url(),
            "tier": _paper_tier(paper),
            "queued_at": imported_at.isoformat(),
        }
        queue["papers"].append(item)
    item.update(
        {
            "status": "imported",
            "imported_at": imported_at.isoformat(),
            "local_pdf": str(target),
            "discovered_local_download": True,
            "source_file": source.name,
        }
    )
    for key in (
        "skipped_at",
        "skip_reason",
        "public_recovery_attempted",
        "public_recovery_failed_at",
        "public_recovery_failure_reason",
        "public_recovery_timeout_count",
        "public_recovery_last_timeout_at",
    ):
        item.pop(key, None)
    _refresh_queue_priorities(queue, issue_date)
    _write_json(_queue_path(), queue)
    return target


def queue_status() -> dict:
    issue_date = _issue_date()
    queue = _load_queue(issue_date)
    _refresh_queue_priorities(queue, issue_date)
    _write_json(_queue_path(), queue)
    imports = _recent_imports()
    return {
        **queue,
        "rate_limit": {
            "max_per_issue": _max_per_issue(),
            "max_per_24_hours": _max_per_24_hours(),
            "min_interval_seconds": _min_interval_seconds(),
            "issue_import_count": _issue_import_count(issue_date),
            "rolling_24_hour_count": len(imports),
        },
    }


def has_terminal_recovery_failure(doi: str) -> bool:
    """Return whether a current-issue DOI has a genuine non-retryable outcome.

    Imported records are usable local recovery assets. A skipped record blocks
    only after a direct publisher attempt in the current authorized MyLOFT
    session; older discovery/session notes do not eliminate a current strict
    candidate before that direct path is tried.
    """

    normalized = _canonical_doi(doi)
    if not normalized:
        return False
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized:
            continue
        return item.get("status") == "skipped" and bool(item.get("direct_publisher_attempted"))
    return False


def has_exhausted_recovery_paths(doi: str) -> bool:
    """Return whether both permitted recovery layers have been exhausted.

    A direct MyLOFT publisher failure releases the same-work public recovery;
    it must not itself remove the paper from selection. Only after that public
    recovery has also completed unsuccessfully may strict selection replace the
    paper with a same-Tier candidate.
    """

    normalized = _canonical_doi(doi)
    if not normalized:
        return False
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized:
            continue
        return bool(
            item.get("status") == "skipped"
            and item.get("direct_publisher_attempted")
            and item.get("public_recovery_attempted")
        )
    return False


def exhausted_recovery_venues(minimum: int = 3) -> set[str]:
    """Return current-issue venues repeatedly unavailable through both paths.

    This is a narrow source-availability circuit breaker, not a relevance or
    venue-quality rule.  It applies only after ``minimum`` separate formal
    papers from one exact venue have each failed the authorized publisher path
    and approved same-work public recovery in this issue.  The selector can
    then use another candidate in the same configured Tier instead of cycling
    through a publisher that is demonstrably unavailable for the issue.
    """

    threshold = max(1, int(minimum))
    queue = _load_queue(_issue_date())
    counts = Counter(
        _venue_key(item.get("venue", ""))
        for item in queue["papers"]
        if (
            item.get("status") == "skipped"
            and item.get("direct_publisher_attempted")
            and item.get("public_recovery_attempted")
            and _venue_key(item.get("venue", ""))
        )
    )
    return {venue for venue, count in counts.items() if count >= threshold}


def has_imported_recovery(doi: str) -> bool:
    """Return whether the current issue already has a validated local import."""

    normalized = _canonical_doi(doi)
    if not normalized:
        return False
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized:
            continue
        local_pdf = Path(item.get("local_pdf") or "")
        return item.get("status") == "imported" and local_pdf.is_file()
    return False


def mark_public_recovery_terminal(doi: str, reason: str) -> bool:
    """Record that approved same-work public recovery was attempted and failed."""

    normalized = _canonical_doi(doi)
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized:
            continue
        if item.get("status") != "skipped" or not item.get("direct_publisher_attempted"):
            return False
        item["public_recovery_attempted"] = True
        item["public_recovery_failed_at"] = _now().isoformat()
        item["public_recovery_failure_reason"] = reason
        _refresh_queue_priorities(queue, _issue_date())
        _write_json(_queue_path(), queue)
        return True
    return False


def record_public_recovery_timeout(doi: str, limit: int = 2) -> bool:
    """Count public-recovery timeouts and terminate the path after ``limit``."""

    normalized = _canonical_doi(doi)
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized:
            continue
        if item.get("status") != "skipped" or not item.get("direct_publisher_attempted"):
            return False
        count = int(item.get("public_recovery_timeout_count") or 0) + 1
        item["public_recovery_timeout_count"] = count
        item["public_recovery_last_timeout_at"] = _now().isoformat()
        if count >= max(1, int(limit)):
            item["public_recovery_attempted"] = True
            item["public_recovery_failed_at"] = _now().isoformat()
            item["public_recovery_failure_reason"] = "approved public recovery timed out repeatedly"
        _refresh_queue_priorities(queue, _issue_date())
        _write_json(_queue_path(), queue)
        return bool(item.get("public_recovery_attempted"))
    return False


def skip_candidate(doi: str, reason: str) -> bool:
    """Mark one pending candidate as skipped without downloading it."""

    normalized = _canonical_doi(doi)
    queue = _load_queue(_issue_date())
    for item in queue["papers"]:
        if _canonical_doi(item.get("doi", "")) != normalized or item.get("status") != "pending":
            continue
        item["status"] = "skipped"
        item["skipped_at"] = _now().isoformat()
        item["skip_reason"] = reason
        item["direct_publisher_attempted"] = True
        _refresh_queue_priorities(queue, _issue_date())
        _write_json(_queue_path(), queue)
        return True
    return False


def validate_pdf_identity(
    payload: bytes,
    expected_title: str,
    doi: str,
    allow_strong_title_without_doi: bool = False,
) -> tuple[bool, str]:
    if len(payload) < 10_000 or not payload.lstrip().startswith(b"%PDF"):
        return False, "invalid signature or file smaller than 10 KB"
    try:
        reader = PdfReader(BytesIO(payload))
        if len(reader.pages) < 2:
            return False, "fewer than 2 pages"
        page_text = "\n".join((page.extract_text() or "") for page in reader.pages[:3])
        metadata_text = " ".join(
            str(value or "")
            for value in (
                getattr(reader.metadata, "title", ""),
                getattr(reader.metadata, "subject", ""),
            )
        )
    except Exception as exc:
        return False, f"unparseable PDF: {type(exc).__name__}"
    text = f"{metadata_text}\n{page_text}".strip()
    if len(text) < 500:
        return False, "less than 500 characters of parseable text"
    title_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", (expected_title or "").lower())
        if len(token) >= 4
    }
    text_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    coverage = sum(token in text_tokens for token in title_tokens) / max(1, len(title_tokens))
    if title_tokens and coverage < 0.5:
        return False, f"title token coverage too low ({coverage:.2f})"
    normalized_text = re.sub(r"\s+", "", text.lower())
    canonical_doi = _canonical_doi(doi)
    if canonical_doi not in normalized_text:
        # Some publisher PDFs (notably current AAAS exports) print only the
        # journal article identifier, e.g. ``eaee5907``, rather than the full
        # DOI. Accept that form only when the title already matches strongly.
        article_id = canonical_doi.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
        compact_text = re.sub(r"[^a-z0-9]+", "", text.lower())
        has_article_id = len(article_id) >= 6 and article_id in compact_text
        if allow_strong_title_without_doi and coverage >= 0.9:
            return True, "ok_strong_title_identity"
        if coverage < 0.75 or not has_article_id:
            return False, "DOI or DOI article identifier not found in PDF metadata or first 3 pages"
    return True, "ok"


def _enforce_rate_limit(issue_date: str) -> None:
    imports = _recent_imports()
    if len(imports) >= _max_per_24_hours():
        raise RuntimeError("MyLOFT rolling 24-hour import limit reached")
    if _issue_import_count(issue_date) >= _max_per_issue():
        raise RuntimeError("MyLOFT per-issue import limit reached")
    if imports:
        last = datetime.fromisoformat(imports[-1]["imported_at"])
        elapsed = (_now() - last).total_seconds()
        if elapsed < _min_interval_seconds():
            remaining = int(_min_interval_seconds() - elapsed + 0.999)
            raise RuntimeError(f"Wait {remaining}s before importing the next MyLOFT PDF")


def _record_import(issue_date: str, doi: str, imported_at: datetime) -> None:
    ledger = _load_json(_ledger_path(), {"schema_version": 1, "imports": []})
    imports = ledger.get("imports") if isinstance(ledger.get("imports"), list) else []
    imports.append({"issue_date": issue_date, "doi": doi, "imported_at": imported_at.isoformat()})
    cutoff = imported_at - timedelta(days=30)
    ledger["imports"] = [
        item
        for item in imports
        if _parse_datetime(item.get("imported_at")) and _parse_datetime(item.get("imported_at")) >= cutoff
    ]
    _write_json(_ledger_path(), ledger)


def _recent_imports() -> list[dict]:
    ledger = _load_json(_ledger_path(), {"imports": []})
    imports = ledger.get("imports") if isinstance(ledger.get("imports"), list) else []
    cutoff = _now() - timedelta(hours=24)
    recent = [item for item in imports if _parse_datetime(item.get("imported_at")) and _parse_datetime(item.get("imported_at")) >= cutoff]
    return sorted(recent, key=lambda item: item["imported_at"])


def _issue_import_count(issue_date: str) -> int:
    ledger = _load_json(_ledger_path(), {"imports": []})
    imports = ledger.get("imports") if isinstance(ledger.get("imports"), list) else []
    return sum(item.get("issue_date") == issue_date for item in imports)


def _load_queue(issue_date: str) -> dict:
    default = {
        "schema_version": 1,
        "issue_date": issue_date,
        "mode": "visible_browser_sequential_only",
        "papers": [],
    }
    path = _queue_path()
    if not path.exists() and not os.getenv("PAPER_RADAR_MYLOFT_QUEUE_PATH"):
        legacy = _load_json(LEGACY_QUEUE_PATH, {})
        if legacy.get("issue_date") == issue_date and isinstance(legacy.get("papers"), list):
            _write_json(path, legacy)
    queue = _load_json(path, default)
    if queue.get("issue_date") != issue_date or not isinstance(queue.get("papers"), list):
        return default
    return queue


def _refresh_queue_priorities(queue: dict, issue_date: str) -> None:
    remaining_budget = max(0, _max_per_issue() - _issue_import_count(issue_date))
    pending = [item for item in queue["papers"] if item.get("status") == "pending"]
    pending.sort(
        key=lambda item: (
            float(item.get("relevance_score") or 0.0),
            1 if item.get("tier") == "tier1" else 0,
            float(item.get("total_score") or 0.0),
            item.get("published_at") or "",
        ),
        reverse=True,
    )
    queued_times = [_parse_datetime(item.get("queued_at")) for item in pending]
    base_time = min((value for value in queued_times if value), default=_now())
    for index, item in enumerate(pending, start=1):
        item["priority_rank"] = index
        item["download_eligible"] = index <= remaining_budget
        item["not_before"] = (
            base_time + timedelta(seconds=(index - 1) * _min_interval_seconds())
        ).isoformat()
    terminal = [item for item in queue["papers"] if item.get("status") != "pending"]
    for item in terminal:
        item["download_eligible"] = False
    queue["papers"] = pending + terminal


def _refresh_pending_entry(item: dict, paper: Paper, doi: str, reason: str, tier: str) -> None:
    metadata = paper.raw.get("paper_radar_recovery", {}) if isinstance(paper.raw, dict) else {}
    item.update(
        {
            "doi": doi,
            "title": paper.title,
            "venue": paper.venue,
            "publisher_url": paper.url or f"https://doi.org/{doi}",
            "portal_url": _portal_url(),
            "reason": reason,
            "tier": tier,
            "total_score": float(metadata.get("total_score") or 0.0),
            "relevance_score": float(metadata.get("relevance_score") or 0.0),
            "published_at": metadata.get("published_at") or paper.published_at.isoformat(),
            "semantic_audited_at": _now().isoformat(),
        }
    )


def _is_stale_semantic_retirement(item: dict) -> bool:
    return (
        item.get("status") == "skipped"
        and item.get("skip_reason") == "absent_from_current_strict_semantic_candidate_set"
    )


def _paper_tier(paper: Paper) -> str:
    if isinstance(paper.raw, dict):
        metadata = paper.raw.get("paper_radar_recovery")
        if isinstance(metadata, dict) and metadata.get("tier"):
            return str(metadata["tier"])
    venue = paper.venue or ""
    if _venue_matches(venue, _profile_setting("tier1_broad_venues", [])):
        return "tier1"
    if _venue_matches(venue, _profile_setting("preferred_venues", [])):
        return "tier2"
    return "other"


def _venue_matches(venue: str, configured) -> bool:
    normalized = " ".join((venue or "").lower().replace("&", "and").split())
    tokens = set(normalized.split())
    for candidate in configured if isinstance(configured, list) else []:
        preferred = " ".join((candidate or "").lower().replace("&", "and").split())
        if preferred in {"nature", "science", "cell"}:
            if normalized == preferred:
                return True
        elif preferred.isalnum() and len(preferred) <= 5:
            if preferred in tokens:
                return True
        elif preferred and preferred in normalized:
            return True
    return False


def _venue_key(venue: str) -> str:
    return " ".join((venue or "").casefold().split())


def _load_json(path: Path, default: dict) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _canonical_doi(value: str) -> str:
    cleaned = (value or "").strip().lower()
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned)
    cleaned = re.sub(r"^doi:\s*", "", cleaned)
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", cleaned)
    return match.group(0).rstrip(".,;)") if match else cleaned


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value or "")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _issue_date() -> str:
    return os.getenv("PAPER_RADAR_RUN_DATE") or os.getenv("PAPER_RADAR_ISSUE_DATE") or date.today().isoformat()


def _enabled() -> bool:
    configured = "1" if _profile_setting("enabled", True) else "0"
    return (os.getenv("PAPER_RADAR_MYLOFT_ENABLED") or configured).strip().lower() not in {"0", "false", "no", "off"}


def _max_per_issue() -> int:
    return _positive_int("PAPER_RADAR_MYLOFT_MAX_PER_ISSUE", _profile_setting("max_downloads_per_issue", 8))


def _max_per_24_hours() -> int:
    return _positive_int("PAPER_RADAR_MYLOFT_MAX_PER_24_HOURS", _profile_setting("max_downloads_per_24_hours", 10))


def _min_interval_seconds() -> int:
    return _positive_int("PAPER_RADAR_MYLOFT_MIN_INTERVAL_SECONDS", _profile_setting("min_interval_seconds", 60))


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int((os.getenv(name) or str(default)).strip()))
    except ValueError:
        return default


def _portal_url() -> str:
    return os.getenv("PAPER_RADAR_MYLOFT_PORTAL_URL") or str(_profile_setting("portal_url", DEFAULT_PORTAL_URL))


def _queue_path() -> Path:
    override = os.getenv("PAPER_RADAR_MYLOFT_QUEUE_PATH")
    if override:
        return Path(override)
    root = Path(os.getenv("PAPER_RADAR_STATE_ROOT") or "data/issues")
    return root / _issue_date() / "myloft-queue.json"


def _ledger_path() -> Path:
    return Path(os.getenv("PAPER_RADAR_MYLOFT_LEDGER_PATH") or DEFAULT_LEDGER_PATH)


def _profile_setting(name: str, default):
    try:
        payload = json.loads(Path("config/profile.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(payload, dict):
        return default
    settings = payload.get("myloft")
    if isinstance(settings, dict) and name in settings:
        return settings[name]
    return payload.get(name, default)
