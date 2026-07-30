import argparse
import base64
import html
import json
import mimetypes
import os
import re
import shutil
import time
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import requests

from .db import init_db
from .db import paper_pk
from .config import load_profile
from .packet import load_analyses, load_issue_editorial, load_research_packet
from .pipeline import run
from .pipeline import current_run_date
from .pipeline import check_research_packet_history
from .pipeline import warm_candidate_cache_from_database
from .rendering import render_outputs
from .export import export_digest
from .myloft import import_download as import_myloft_download
from .myloft import queue_status as myloft_queue_status
from .myloft import skip_candidate as skip_myloft_candidate
from .state import compact_run_report, mark_stage, record_run_event


def main() -> None:
    parser = argparse.ArgumentParser(prog="paper-radar")
    parser.add_argument(
        "--issue-date",
        dest="run_issue_date",
        default=os.getenv("PAPER_RADAR_RUN_DATE") or os.getenv("PAPER_RADAR_ISSUE_DATE") or "",
        help="Issue date as YYYY-MM-DD, today, upcoming-sunday, or previous-sunday.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-db", help="Initialize SQLite database.")
    init_parser.add_argument("--db-path", default=os.getenv("PAPER_RADAR_DB_PATH", "data/paper_radar.db"))

    run_parser = subparsers.add_parser("run", help="Run the weekly candidate pipeline.")
    run_parser.add_argument("--db-path", default=os.getenv("PAPER_RADAR_DB_PATH", "data/paper_radar.db"))
    run_parser.add_argument("--fixture", action="store_true", help="Use local sample papers instead of live APIs.")
    run_parser.add_argument("--refresh-sources", action="store_true", help="Ignore the issue candidate cache and refetch the full configured window.")

    prepare_parser = subparsers.add_parser("prepare-weekly", help="Prepare candidate packet for Codex analysis.")
    prepare_parser.add_argument("--db-path", default=os.getenv("PAPER_RADAR_DB_PATH", "data/paper_radar.db"))
    prepare_parser.add_argument("--fixture", action="store_true", help="Use local sample papers instead of live APIs.")
    prepare_parser.add_argument("--refresh-sources", action="store_true", help="Ignore the issue candidate cache and refetch the full configured window.")

    warm_parser = subparsers.add_parser(
        "warm-candidate-cache",
        help="Build a bounded metadata candidate catalog from the local database without network access.",
    )
    warm_parser.add_argument("--db-path", default=os.getenv("PAPER_RADAR_DB_PATH", "data/paper_radar.db"))
    warm_parser.add_argument("--force", action="store_true", help="Replace an existing non-empty cache for this issue date.")

    subparsers.add_parser("run-report", help="Print the compact resumable state and recent run events for this issue.")
    stage_parser = subparsers.add_parser("mark-stage", help="Record a later workflow stage such as publishing or email delivery.")
    stage_parser.add_argument("--stage", required=True)
    stage_parser.add_argument(
        "--status",
        required=True,
        choices=["pending", "in_progress", "partial", "complete", "failed", "blocked"],
    )
    stage_parser.add_argument("--detail", default="", help="Optional short human-readable detail.")

    history_parser = subparsers.add_parser(
        "history-check",
        help="Check the current packet against a compact local archive index.",
    )
    history_parser.add_argument("--packet", default="output/research_packet.json")
    history_parser.add_argument("--site-dir", default="site")
    history_parser.add_argument("--issue-date", default=os.getenv("PAPER_RADAR_RUN_DATE", ""))

    candidate_audit_parser = subparsers.add_parser(
        "candidate-audit",
        help="Print the compact metadata-only semantic audit for the prepared packet.",
    )
    candidate_audit_parser.add_argument("--path", default="output/candidate_audit.json")
    figure_audit_parser = subparsers.add_parser(
        "figure-audit",
        help="Print the compact verified-Figure-1 audit for the prepared packet.",
    )
    figure_audit_parser.add_argument("--path", default="output/figure_audit.json")

    subparsers.add_parser("myloft-status", help="Show the small, rate-limited MyLOFT recovery queue.")
    myloft_import_parser = subparsers.add_parser(
        "myloft-import",
        help="Validate and import one PDF downloaded through the visible authorized MyLOFT browser session.",
    )
    myloft_import_parser.add_argument("--doi", required=True)
    myloft_import_parser.add_argument("--pdf", required=True)
    myloft_skip_parser = subparsers.add_parser(
        "myloft-skip",
        help="Skip one inaccessible MyLOFT candidate so a later rerun can queue another.",
    )
    myloft_skip_parser.add_argument("--doi", required=True)
    myloft_skip_parser.add_argument("--reason", required=True)

    render_parser = subparsers.add_parser("render-from-analyses", help="Render outputs from Codex-authored analyses.json.")
    render_parser.add_argument("--db-path", default=os.getenv("PAPER_RADAR_DB_PATH", "data/paper_radar.db"))

    bundle_parser = subparsers.add_parser("bundle-email", help="Bundle digest assets for email attachment.")
    bundle_parser.add_argument("--output", default="output/paper-radar-digest.zip")
    bundle_parser.add_argument(
        "--include-all-figures",
        action="store_true",
        help="Bundle every cached figure under output/figures instead of only figures referenced by the current packet.",
    )

    clean_parser = subparsers.add_parser("clean-workspace", help="Clean transient output artifacts without touching archives.")
    clean_parser.add_argument("--older-than-days", type=int, default=30)
    clean_parser.add_argument("--apply", action="store_true", help="Actually remove files. Default is dry-run.")

    site_parser = subparsers.add_parser("build-site", help="Build static site assets for public hosting.")
    site_parser.add_argument("--site-dir", default="site")
    site_parser.add_argument("--public-url", default=os.getenv("PAPER_RADAR_PUBLIC_URL", ""))
    site_parser.add_argument(
        "--issue-date",
        default=os.getenv("PAPER_RADAR_ISSUE_DATE", ""),
        help="Archive date slug, YYYY-MM-DD. Defaults to date detected from output/digest.html.",
    )
    verify_parser = subparsers.add_parser("verify-publication", help="Verify that the public latest/issue/archive pages have refreshed.")
    verify_parser.add_argument("--site-dir", default="site")
    verify_parser.add_argument(
        "--public-url",
        default=os.getenv("PAPER_RADAR_PUBLIC_URL", ""),
        help="Public site root, e.g. https://guorong-he.github.io/paper-radar/",
    )
    verify_parser.add_argument(
        "--issue-date",
        default=os.getenv("PAPER_RADAR_ISSUE_DATE", ""),
        help="Archive date slug, YYYY-MM-DD. Defaults to date detected from output/digest.html.",
    )
    verify_parser.add_argument("--retries", type=int, default=int(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_RETRIES", "6")))
    verify_parser.add_argument("--delay-seconds", type=float, default=float(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_DELAY_SECONDS", "20")))
    verify_parser.add_argument("--timeout", type=int, default=int(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_TIMEOUT_SECONDS", "20")))
    verify_parser.add_argument("--output", default="output/public_verification.json")

    args = parser.parse_args()
    try:
        resolved_run_date = resolve_run_issue_date(args.run_issue_date)
    except ValueError as exc:
        parser.error(str(exc))
    os.environ["PAPER_RADAR_RUN_DATE"] = resolved_run_date.isoformat()
    os.environ["PAPER_RADAR_ISSUE_DATE"] = resolved_run_date.isoformat()
    if args.command == "init-db":
        init_db(args.db_path)
        print(f"Initialized database at {args.db_path}")
    elif args.command == "run":
        selected = run(
            args.db_path,
            fixture=args.fixture,
            refresh_sources=True if args.refresh_sources else None,
        )
        print(f"Selected {len(selected)} papers")
        for idx, item in enumerate(selected, start=1):
            print(f"{idx:02d}. {item.paper.title} [{item.score.total_score}]")
    elif args.command == "prepare-weekly":
        selected = run(
            args.db_path,
            fixture=args.fixture,
            analyze=False,
            refresh_sources=True if args.refresh_sources else None,
        )
        packet_count = _research_packet_count()
        if packet_count != len(selected):
            print(f"Prepared research packet with {packet_count} papers; live selection produced {len(selected)}")
        else:
            print(f"Prepared research packet with {len(selected)} papers")
    elif args.command == "warm-candidate-cache":
        report = warm_candidate_cache_from_database(
            args.db_path,
            load_profile(),
            current_run_date(),
            force=args.force,
        )
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    elif args.command == "run-report":
        print(json.dumps(compact_run_report(current_run_date()), ensure_ascii=False, separators=(",", ":")))
    elif args.command == "mark-stage":
        details = {"detail": args.detail} if args.detail else {}
        mark_stage(current_run_date(), args.stage, args.status, details)
        record_run_event(current_run_date(), "external", args.stage, args.status, details)
        print(f"Recorded {args.stage}={args.status}")
    elif args.command == "history-check":
        report = check_research_packet_history(
            packet_path=args.packet,
            today=date.fromisoformat(args.issue_date) if args.issue_date else resolved_run_date,
            site_dir=args.site_dir,
        )
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        if report["overlap_count"]:
            raise SystemExit(2)
    elif args.command == "candidate-audit":
        _print_compact_audit(args.path, "semantic")
    elif args.command == "figure-audit":
        _print_compact_audit(args.path, "figure_one")
    elif args.command == "myloft-status":
        print(json.dumps(myloft_queue_status(), ensure_ascii=False, indent=2))
    elif args.command == "myloft-import":
        imported = import_myloft_download(args.doi, args.pdf)
        print(f"Imported validated MyLOFT PDF at {imported}")
    elif args.command == "myloft-skip":
        if not skip_myloft_candidate(args.doi, args.reason):
            raise SystemExit("No matching pending MyLOFT queue item")
        print(f"Skipped MyLOFT candidate {args.doi}")
    elif args.command == "render-from-analyses":
        selected = load_research_packet()
        analyses = load_analyses("output/analyses.json")
        issue_editorial = load_issue_editorial("output/analyses.json")
        export_digest(selected, analyses, paper_pk)
        render_outputs(selected, analyses, paper_pk, issue_date=current_run_date(), issue_editorial=issue_editorial)
        mark_stage(current_run_date(), "analysis_render", "complete", {"paper_count": len(selected)})
        record_run_event(current_run_date(), "external", "analysis_render", "completed", {"paper_count": len(selected)})
        print(f"Rendered outputs from analyses for {len(selected)} papers")
    elif args.command == "bundle-email":
        bundle_digest_assets(args.output, include_all_figures=args.include_all_figures)
        print(f"Bundled digest assets at {args.output}")
    elif args.command == "clean-workspace":
        cleanup_workspace(older_than_days=args.older_than_days, apply=args.apply)
    elif args.command == "build-site":
        _ensure_publishable_prepare_status()
        issue_date = _resolve_issue_date(args.issue_date or resolved_run_date.isoformat())
        urls = build_static_site(args.site_dir, issue_date=issue_date)
        if args.public_url:
            issue_url = _join_url(args.public_url, f"issues/{issue_date.isoformat()}/")
            latest_url = _join_url(args.public_url, "latest/")
            archive_url = _join_url(args.public_url, "issues/")
            write_link_email(issue_url, latest_url=latest_url, archive_url=archive_url)
            print(f"Built permanent-link email for {issue_url}")
        mark_stage(current_run_date(), "site_build", "complete", {"issue_date": issue_date.isoformat()})
        record_run_event(current_run_date(), "external", "site_build", "completed", {"issue_date": issue_date.isoformat()})
        print(f"Built static site at {urls['latest']} and {urls['issue']}")
    elif args.command == "verify-publication":
        report = verify_publication(
            args.public_url,
            site_dir=args.site_dir,
            issue_date=_resolve_issue_date(args.issue_date or resolved_run_date.isoformat()),
            retries=args.retries,
            delay_seconds=args.delay_seconds,
            timeout=args.timeout,
            output_path=args.output,
        )
        mark_stage(current_run_date(), "publication_verify", "complete", {"issue_date": report["issue_date"]})
        record_run_event(current_run_date(), "external", "publication_verify", "completed", {"issue_date": report["issue_date"]})
        print(f"Verified publication for {report['urls']['issue']}")


def resolve_run_issue_date(value: str, *, today: date | None = None) -> date:
    anchor = today or date.today()
    normalized = (value or "today").strip().lower()
    if normalized == "today":
        return anchor
    if normalized == "upcoming-sunday":
        return anchor + timedelta(days=(6 - anchor.weekday()) % 7)
    if normalized == "previous-sunday":
        days = (anchor.weekday() - 6) % 7 or 7
        return anchor - timedelta(days=days)
    try:
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "--issue-date must be YYYY-MM-DD, today, upcoming-sunday, or previous-sunday"
        ) from exc


def bundle_digest_assets(output_path: str, include_all_figures: bool = False) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(out, "w", compression=ZIP_DEFLATED) as zf:
        for name in [
            "digest.html",
            "email.html",
            "email-link.html",
            "email-fallback.html",
            "digest.json",
            "digest.md",
            "research_packet.json",
            "analyses.json",
        ]:
            path = Path("output") / name
            if path.exists():
                zf.write(path, arcname=name)
        figures = _bundle_figure_paths(include_all_figures=include_all_figures)
        for image in figures:
            zf.write(image, arcname=f"figures/{image.name}")


def _research_packet_count(path: str = "output/research_packet.json") -> int:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(payload) if isinstance(payload, list) else 0


def _print_compact_audit(path: str, audit_type: str) -> None:
    """Expose only the concise evidence needed for final gate review."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Missing or invalid {audit_type} audit at {path}: {exc}") from exc
    if not isinstance(payload, list):
        raise SystemExit(f"Invalid {audit_type} audit at {path}: expected a list")
    rejected = [
        {
            "paper_id": record.get("paper_id", ""),
            "reason": record.get("reason", ""),
        }
        for record in payload
        if not isinstance(record, dict) or not record.get("accepted")
    ]
    report = {
        "audit": audit_type,
        "paper_count": len(payload),
        "accepted_count": len(payload) - len(rejected),
        "rejected": rejected,
    }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    if rejected:
        raise SystemExit(2)


def _ensure_publishable_prepare_status(path: str = "output/prepare_status.json") -> None:
    if os.getenv("PAPER_RADAR_ALLOW_STALE_ISSUE", "0") == "1":
        return
    status_path = Path(path)
    if not status_path.exists():
        raise SystemExit(
            f"Missing prepare status at {path}. Run prepare-weekly first, or set "
            "PAPER_RADAR_ALLOW_STALE_ISSUE=1 only for an intentional manual rebuild."
        )
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid prepare status at {path}: {exc}") from exc
    if status.get("preserved_existing_packet"):
        raise SystemExit(
            "Refusing to build a new issue from a preserved previous packet. "
            f"Live selection produced {status.get('live_selection_count')} papers, "
            f"target is {status.get('target_count')}. "
            "Rerun prepare-weekly when sources recover, or set PAPER_RADAR_ALLOW_STALE_ISSUE=1 "
            "only if you intentionally want to republish stale content."
        )
    if status.get("ready_to_publish") is False:
        raise SystemExit(
            "Refusing to build site because prepare-weekly did not produce a publishable packet. "
            f"Status: {status}"
        )


def build_static_site(site_dir: str = "site", issue_date: date | None = None) -> dict[str, str]:
    issue_date = issue_date or _resolve_issue_date("")
    site_root = Path(site_dir)
    latest_root = site_root / "latest"
    issue_root = site_root / "issues" / issue_date.isoformat()

    for root, archive_href in (
        (latest_root, "../issues/index.html"),
        (issue_root, "../index.html"),
    ):
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        _write_site_issue(root, archive_href=archive_href)

    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / ".nojekyll").write_text("", encoding="utf-8")
    _write_history_index(site_root)
    (site_root / "index.html").write_text(_render_home_redirect(issue_date), encoding="utf-8")
    (site_root / "manifest.json").write_text(
        '{\n'
        f'  "latest": "latest/",\n'
        f'  "archive": "issues/",\n'
        f'  "current_issue": "issues/{issue_date.isoformat()}/",\n'
        f'  "issue_date": "{issue_date.isoformat()}"\n'
        '}\n',
        encoding="utf-8",
    )
    return {"latest": str(latest_root), "issue": str(issue_root)}


def verify_publication(
    public_url: str,
    site_dir: str = "site",
    issue_date: date | None = None,
    retries: int = 6,
    delay_seconds: float = 20.0,
    timeout: int = 20,
    output_path: str = "output/public_verification.json",
) -> dict:
    if not public_url:
        raise SystemExit("Missing --public-url for verify-publication")
    issue_date = issue_date or _resolve_issue_date("")
    expected_packet = _load_expected_public_packet(site_dir, issue_date)
    urls = _public_urls(public_url, issue_date)
    session = requests.Session()
    session.trust_env = False
    report = {}
    try:
        for attempt in range(1, max(1, retries) + 1):
            report = _verify_publication_once(
                session,
                urls=urls,
                expected_packet=expected_packet,
                issue_date=issue_date,
                timeout=timeout,
            )
            report["attempt"] = attempt
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if report["ok"]:
                return report
            if attempt < max(1, retries):
                time.sleep(max(0.0, delay_seconds))
    finally:
        session.close()
    raise SystemExit("Public verification failed: " + "; ".join(report.get("errors", [])[:6]))


def _verify_publication_once(
    session: requests.Session,
    *,
    urls: dict[str, str],
    expected_packet: list[dict],
    issue_date: date,
    timeout: int,
) -> dict:
    errors: list[str] = []
    html_results = {}
    packet_results = {}

    for label, url in [("issue_html", urls["issue"]), ("latest_html", urls["latest"]), ("archive_html", urls["archive"])]:
        status, text, error = _fetch_text(session, url, timeout)
        html_results[label] = {"status": status, "error": error}
        errors.extend(_public_html_errors(label, status, text, issue_date))
        if error:
            errors.append(f"{label}: {error}")

    expected_titles = [paper.get("title") or "" for paper in expected_packet]
    expected_count = len(expected_packet)
    for label, url in [("issue_packet", urls["issue_packet"]), ("latest_packet", urls["latest_packet"])]:
        status, payload, error = _fetch_json(session, url, timeout)
        normalized = _normalize_public_packet(payload)
        packet_results[label] = {"status": status, "error": error, "count": len(normalized)}
        if error:
            errors.append(f"{label}: {error}")
        errors.extend(_public_packet_errors(label, status, normalized, expected_titles, expected_count))

    return {
        "ok": not errors,
        "issue_date": issue_date.isoformat(),
        "urls": urls,
        "html": html_results,
        "packets": packet_results,
        "errors": errors,
    }


def _public_urls(public_url: str, issue_date: date) -> dict[str, str]:
    return {
        "issue": _join_url(public_url, f"issues/{issue_date.isoformat()}/"),
        "latest": _join_url(public_url, "latest/"),
        "archive": _join_url(public_url, "issues/"),
        "issue_packet": _join_url(public_url, f"issues/{issue_date.isoformat()}/research_packet.json"),
        "latest_packet": _join_url(public_url, "latest/research_packet.json"),
    }


def _load_expected_public_packet(site_dir: str, issue_date: date) -> list[dict]:
    packet_path = Path(site_dir) / "issues" / issue_date.isoformat() / "research_packet.json"
    try:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing expected public packet at {packet_path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid expected public packet at {packet_path}: {exc}") from exc
    papers = _normalize_public_packet(payload)
    if not papers:
        raise SystemExit(f"Expected public packet at {packet_path} is empty")
    return papers


def _fetch_text(session: requests.Session, url: str, timeout: int) -> tuple[int | None, str, str]:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        return response.status_code, response.text, ""
    except requests.RequestException as exc:
        return None, "", str(exc)


def _fetch_json(session: requests.Session, url: str, timeout: int) -> tuple[int | None, object, str]:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return None, None, str(exc)
    try:
        return response.status_code, response.json(), ""
    except ValueError as exc:
        return response.status_code, None, f"invalid json: {exc}"


def _normalize_public_packet(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        papers = payload.get("papers", [])
        if isinstance(papers, list):
            return [item for item in papers if isinstance(item, dict)]
    return []


def _public_html_errors(label: str, status: int | None, text: str, issue_date: date) -> list[str]:
    if status != 200:
        return [f"{label}: expected 200, got {status}"]
    required = ["--paper"]
    if label == "archive_html":
        required.append("Paper Radar · Journal Archive")
    else:
        required.extend(["Paper Radar · Journal Edition", 'id="archive-link"', "历史推荐"])
        if label == "issue_html":
            required.append(issue_date.isoformat())
    missing = [marker for marker in required if marker not in text]
    return [f"{label}: missing {marker}" for marker in missing]


def _public_packet_errors(
    label: str,
    status: int | None,
    papers: list[dict],
    expected_titles: list[str],
    expected_count: int,
) -> list[str]:
    if status != 200:
        return [f"{label}: expected 200, got {status}"]
    if len(papers) != expected_count:
        return [f"{label}: expected {expected_count} papers, got {len(papers)}"]
    titles = [paper.get("title") or "" for paper in papers]
    errors = []
    if titles != expected_titles:
        errors.append(f"{label}: titles do not match local issue packet")
    if any(not paper.get("key_figure_path") for paper in papers):
        errors.append(f"{label}: missing key_figure_path in public packet")
    if any("fulltext" in paper and paper.get("fulltext") for paper in papers):
        errors.append(f"{label}: public packet still exposes fulltext")
    return errors


def _write_history_index(site_root: Path) -> None:
    issues_root = site_root / "issues"
    issues_root.mkdir(parents=True, exist_ok=True)
    summaries = _issue_summaries(issues_root)
    (issues_root / "index.html").write_text(_render_history_index(summaries), encoding="utf-8")


def _issue_summaries(issues_root: Path) -> list[dict]:
    summaries = []
    for packet_path in sorted(issues_root.glob("*/research_packet.json"), reverse=True):
        issue_slug = packet_path.parent.name
        try:
            issue_date = date.fromisoformat(issue_slug)
            payload = json.loads(packet_path.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
        papers = payload if isinstance(payload, list) else payload.get("papers", [])
        if not isinstance(papers, list):
            continue
        summaries.append(
            {
                "date": issue_date,
                "slug": issue_slug,
                "paper_count": len(papers),
                "figure_count": sum(1 for paper in papers if isinstance(paper, dict) and paper.get("key_figure_path")),
                "formal_count": sum(
                    1
                    for paper in papers
                    if isinstance(paper, dict) and (paper.get("source") or "").lower() != "arxiv"
                ),
                "papers": [paper for paper in papers if isinstance(paper, dict)],
            }
        )
    return summaries


def _render_history_index(summaries: list[dict]) -> str:
    issue_cards = "\n".join(_render_history_issue(summary) for summary in summaries)
    total_papers = sum(summary["paper_count"] for summary in summaries)
    total_figures = sum(summary["figure_count"] for summary in summaries)
    total_formal = sum(summary["formal_count"] for summary in summaries)
    if not issue_cards:
        issue_cards = '<section class="empty">暂无历史推荐。</section>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Paper Radar · 历史推荐</title>
  <style>
    :root {{
      color-scheme: light;
      --paper:#f4f1e8;
      --paper-strong:#fbfaf4;
      --ink:#181410;
      --ink-soft:#4a4037;
      --muted:#73695f;
      --line:#2a2119;
      --line-soft:rgba(42,33,25,.24);
      --rule:rgba(42,33,25,.44);
      --accent:#a93522;
      --accent-dark:#401d15;
      --signal:#2d6d65;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background:
        linear-gradient(90deg, var(--accent) 0 18px, transparent 18px),
        repeating-linear-gradient(0deg, rgba(42,33,25,.026), rgba(42,33,25,.026) 1px, transparent 1px, transparent 8px),
        var(--paper);
      color: var(--ink);
      font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", serif;
    }}
    .wrap {{ width: min(1150px, calc(100vw - 40px)); margin: 0 auto; }}
    header {{ padding: 44px 0 26px; }}
    .masthead {{
      display: grid;
      grid-template-columns: minmax(0, 1.04fr) minmax(280px, .96fr);
      gap: 42px;
      padding-bottom: 26px;
      border-bottom: 2px solid var(--line);
    }}
    .eyebrow {{
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
      color: var(--accent);
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0 0 16px;
      font-family: "Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif;
      font-size: 74px;
      line-height: .93;
      letter-spacing: 0;
    }}
    .sub {{ margin: 0; max-width: 680px; color: var(--ink-soft); line-height: 1.8; font-size: 19px; }}
    .archive-stats {{
      align-self: end;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }}
    .stat {{ min-height: 104px; border: 1px solid var(--line); background: rgba(255,255,255,.22); padding: 18px 15px; }}
    .stat strong {{ display: block; font-size: 34px; line-height: 1; font-weight: 800; }}
    .stat span {{ display: block; margin-top: 14px; color: var(--muted); font-size: 12px; letter-spacing: 0; text-transform: uppercase; }}
    nav {{ margin-top: 18px; display: flex; gap: 8px; flex-wrap: wrap; }}
    nav a {{ color: var(--ink); text-decoration: none; border: 1px solid var(--line-soft); background: rgba(255,255,255,.24); padding: 8px 10px; font-size: 13px; }}
    nav a.primary {{ background: var(--ink); border-color: var(--ink); color: var(--paper-strong); }}
    main {{ padding: 0 0 58px; }}
    .archive-layout {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 28px; align-items: start; margin-top: 26px; }}
    .issue {{ border-top: 1px solid var(--rule); padding: 18px 0 20px; }}
    .issue-head {{ display: grid; grid-template-columns: 72px minmax(0, 1fr) 170px; gap: 16px; align-items: start; }}
    .issue-date {{ color: var(--accent); font-family: "Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif; font-size: 28px; line-height: 1; }}
    .issue-title {{ margin: 0; font-family: "Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif; font-size: 28px; line-height: 1.16; }}
    .issue-title a {{ color: var(--ink); text-decoration: none; }}
    .issue-title a:hover {{ color: var(--accent); }}
    .stats {{ justify-self: end; border: 1px solid var(--accent); color: var(--accent); padding: 8px 10px; font-size: 12px; line-height: 1.5; text-align: right; }}
    ol {{ list-style: none; margin: 14px 0 0 88px; padding: 0; border-top: 1px solid var(--line-soft); }}
    li {{ display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; padding: 10px 0; line-height: 1.55; border-bottom: 1px solid rgba(42,33,25,.14); }}
    .paper-index {{ color: var(--accent); font-family: "SF Mono", "Menlo", monospace; font-size: 12px; padding-top: 2px; }}
    .paper a {{ color: var(--ink); text-decoration: none; }}
    .paper a:hover {{ color: var(--accent); }}
    .meta {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
    .archive-panel {{
      position: sticky;
      top: 18px;
      min-height: 430px;
      background: linear-gradient(180deg, var(--accent-dark), #17130f);
      color: var(--paper-strong);
      padding: 26px;
      overflow: hidden;
    }}
    .archive-panel::before {{ content: ""; position: absolute; inset: 0; background: linear-gradient(145deg, rgba(169,53,34,.5), transparent 38%); pointer-events: none; }}
    .archive-panel > * {{ position: relative; }}
    .panel-label {{ color: #d2c5b4; font-size: 12px; letter-spacing: 0; text-transform: uppercase; }}
    .panel-title {{ margin: 10px 0 24px; font-family: "Songti SC", "STSong", "Noto Serif CJK SC", Georgia, serif; font-size: 38px; line-height: 1; }}
    .panel-stat {{ border-top: 1px solid rgba(244,241,232,.26); padding: 14px 0; }}
    .panel-stat strong {{ display: block; font-size: 30px; line-height: 1; }}
    .panel-stat span {{ display: block; margin-top: 6px; color: #d2c5b4; font-size: 13px; }}
    .panel-note {{ position: relative; margin-top: 22px; color: #d2c5b4; font-size: 13px; line-height: 1.7; }}
    .empty {{ border: 1px solid var(--line-soft); padding: 24px; color: var(--muted); background: rgba(255,255,255,.18); }}
    @media (max-width: 680px) {{
      .wrap {{ width: auto; margin: 0 24px 0 42px; }}
      header {{ padding-top: 28px; }}
      .masthead {{ grid-template-columns: 1fr; gap: 20px; }}
      h1 {{ font-size: 40px; line-height: 1; }}
      .sub {{ font-size: 16px; }}
      .archive-stats {{ grid-template-columns: repeat(3, 1fr); }}
      .stat {{ min-height: 82px; padding: 12px 10px; }}
      .archive-layout {{ grid-template-columns: 1fr; }}
      .archive-panel {{ display: block; position: relative; top: auto; margin-top: 24px; }}
      .issue-head {{ grid-template-columns: 54px minmax(0, 1fr); }}
      .stats {{ grid-column: 1 / 3; justify-self: start; text-align: left; margin-left: 70px; }}
      ol {{ margin-left: 70px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <section class="masthead">
        <div>
          <div class="eyebrow">Paper Radar · Archive</div>
          <h1>历史推荐<br>归档</h1>
          <p class="sub">按期汇总已发布的 Paper Radar 永久链接和每期推荐论文，方便回看、检索和避免重复阅读。</p>
        </div>
        <div class="archive-stats">
          <div class="stat"><strong>{len(summaries)}</strong><span>issues</span></div>
          <div class="stat"><strong>{total_papers}</strong><span>papers</span></div>
          <div class="stat"><strong>{total_figures}</strong><span>figures</span></div>
        </div>
      </section>
      <nav>
        <a class="primary" href="../latest/index.html">最新一期</a>
        <a href="../index.html">首页</a>
      </nav>
    </div>
  </header>
  <main>
    <div class="wrap archive-layout">
      <div>{issue_cards}</div>
      <aside class="archive-panel">
        <div class="panel-label">Paper Radar · Journal Archive</div>
        <h2 class="panel-title">研究线索<br>时间轴</h2>
        <div class="panel-stat"><strong>{len(summaries)}</strong><span>已归档期数</span></div>
        <div class="panel-stat"><strong>{total_papers}</strong><span>累计推荐论文</span></div>
        <div class="panel-stat"><strong>{total_formal}</strong><span>正式来源论文</span></div>
        <div class="panel-note">每一期都是独立永久链接，邮件中的旧链接不会被 latest 覆盖。</div>
      </aside>
    </div>
  </main>
</body>
</html>"""


def _render_history_issue(summary: dict) -> str:
    issue_href = f"{summary['slug']}/index.html"
    papers = "\n".join(_render_history_paper(paper, idx) for idx, paper in enumerate(summary["papers"], start=1))
    issue_label = summary["slug"][5:] if isinstance(summary.get("slug"), str) and len(summary["slug"]) >= 10 else summary["slug"]
    return f"""<section class="issue">
  <div class="issue-head">
    <div class="issue-date">{html.escape(issue_label)}</div>
    <h2 class="issue-title"><a href="{issue_href}">Paper Radar · {html.escape(summary['slug'])}</a></h2>
    <div class="stats">{summary['paper_count']} 篇 · {summary['figure_count']} 图 · formal {summary['formal_count']}</div>
  </div>
  <ol>
    {papers}
  </ol>
</section>"""


def _render_history_paper(paper: dict, idx: int) -> str:
    title = html.escape(str(paper.get("title") or "Untitled"))
    venue = html.escape(str(paper.get("venue") or paper.get("source") or "Unknown venue"))
    published = html.escape(str(paper.get("published_at") or ""))
    url = str(paper.get("url") or paper.get("pdf_url") or "").strip()
    if url:
        title_html = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
    else:
        title_html = title
    meta = " · ".join(part for part in [venue, published] if part)
    return f'<li><div class="paper-index">{idx:02d}</div><div><div class="paper">{title_html}</div><div class="meta">{meta}</div></div></li>'


def _write_site_issue(root: Path, archive_href: str = "../issues/index.html") -> None:
    copies = [
        (Path("output/email.html"), root / "email.html"),
        (Path("output/digest.json"), root / "digest.json"),
        (Path("output/digest.md"), root / "digest.md"),
        (Path("output/analyses.json"), root / "analyses.json"),
        (Path("output/paper-radar-poster.png"), root / "poster.png"),
    ]
    digest = Path("output/digest.html")
    if digest.exists():
        # For hosted pages, keep HTML light and publish figures as static assets.
        # Inlining was useful for email bundles, but it makes GitHub API uploads fragile.
        html_text = _rewrite_issue_navigation(digest.read_text(encoding="utf-8"), archive_href)
        (root / "index.html").write_text(html_text, encoding="utf-8")
    for source, target in copies:
        if source.exists():
            shutil.copy2(source, target)
    _write_public_research_packet(Path("output/research_packet.json"), root / "research_packet.json")
    for figure in _referenced_figure_paths():
        target = root / "figures" / figure.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(figure, target)


def _rewrite_issue_navigation(html_text: str, archive_href: str) -> str:
    html_text = re.sub(
        r'(<a\b[^>]*\bid="archive-link"[^>]*\bhref=")[^"]*(")',
        rf"\1{archive_href}\2",
        html_text,
        count=1,
    )
    return re.sub(
        r'archiveLink\.href\s*=\s*isPermanentIssue\s*\?\s*"[^"]*"\s*:\s*"[^"]*";',
        f'archiveLink.href = "{archive_href}";',
        html_text,
        count=1,
    )


def _write_public_research_packet(source: Path, target: Path) -> None:
    if not source.exists():
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        shutil.copy2(source, target)
        return
    if not isinstance(payload, list):
        shutil.copy2(source, target)
        return
    public_payload = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        public_item = dict(item)
        public_item.pop("fulltext", None)
        public_payload.append(public_item)
    target.write_text(json.dumps(public_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bundle_figure_paths(include_all_figures: bool = False, packet_path: str = "output/research_packet.json") -> list[Path]:
    if include_all_figures:
        figures = Path("output/figures")
        if not figures.exists():
            return []
        return [image for image in sorted(figures.glob("*")) if image.is_file()]
    return _referenced_figure_paths(packet_path)


def _referenced_figure_paths(packet_path: str = "output/research_packet.json") -> list[Path]:
    try:
        payload = json.loads(Path(packet_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    figures = []
    seen = set()
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        figure = item.get("key_figure_path")
        if not figure:
            continue
        path = Path(figure)
        if not path.exists() or path in seen:
            continue
        seen.add(path)
        figures.append(path)
    return figures


def _inline_figure_assets(html_text: str, output_dir: Path = Path("output")) -> str:
    def repl(match: re.Match) -> str:
        quote = match.group("quote")
        rel = match.group("path")
        local = output_dir / rel
        if not local.exists():
            return match.group(0)
        mime = mimetypes.guess_type(local.name)[0] or "image/png"
        encoded = base64.b64encode(local.read_bytes()).decode("ascii")
        return f"{quote}data:{mime};base64,{encoded}{quote}"

    return re.sub(
        r"(?P<quote>[\"'])(?P<path>figures/[^\"']+\.(?:png|jpg|jpeg|webp|gif))(?P=quote)",
        repl,
        html_text,
        flags=re.IGNORECASE,
    )


def _resolve_issue_date(raw: str) -> date:
    if raw:
        return date.fromisoformat(raw)
    digest = Path("output/digest.html")
    if digest.exists():
        text = digest.read_text(encoding="utf-8")
        match = re.search(r"<title>Paper Radar · (\d{4}-\d{2}-\d{2})</title>", text)
        if match:
            return date.fromisoformat(match.group(1))
    return date.today()


def _render_home_redirect(issue_date: date) -> str:
    issue = issue_date.isoformat()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="0; url=latest/index.html" />
  <title>Paper Radar</title>
  <style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#08111f;color:#f8fafc;display:grid;place-items:center;min-height:100vh;margin:0}}a{{color:#67e8f9}}</style>
</head>
<body>
  <main>
    <h1>Paper Radar</h1>
    <p>正在打开最新一期；本期永久归档：<a href="issues/{issue}/index.html">{issue}</a></p>
    <p><a href="latest/index.html">打开 latest</a> · <a href="issues/index.html">查看历史推荐</a></p>
  </main>
</body>
</html>"""


def _join_url(base: str, suffix: str) -> str:
    return base.rstrip("/") + "/" + suffix.lstrip("/")


def cleanup_workspace(older_than_days: int = 30, apply: bool = False, output_dir: str = "output") -> list[Path]:
    candidates = cleanup_candidates(older_than_days=older_than_days, output_dir=output_dir)
    action = "remove" if apply else "would remove"
    if not candidates:
        print("No transient cleanup candidates found")
        return []
    total_bytes = sum(_path_size(path) for path in candidates)
    print(f"{action} {len(candidates)} transient artifact(s), {total_bytes / (1024 * 1024):.1f} MB")
    for path in candidates:
        print(path)
        if apply:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    return candidates


def cleanup_candidates(older_than_days: int = 30, output_dir: str = "output", now: float | None = None) -> list[Path]:
    root = Path(output_dir)
    if not root.exists():
        return []
    cutoff = (now if now is not None else time.time()) - max(0, older_than_days) * 86400
    candidates = []
    transient_dir_patterns = [
        "*-pages",
        "*-images",
        "paper-lens-checks",
        "argus-debug-figures",
        "email_bundle_*",
    ]
    for pattern in transient_dir_patterns:
        for path in root.glob(pattern):
            if path.is_dir() and path.stat().st_mtime < cutoff:
                candidates.append(path)
    for path in root.glob("paper-radar*.zip"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            candidates.append(path)
    return sorted(set(candidates))


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def write_link_email(
    public_url: str,
    output_path: str = "output/email-link.html",
    latest_url: str = "",
    archive_url: str = "",
) -> None:
    public_url = public_url.rstrip("/") + "/"
    latest_note = (
        f"<div style=\"font-size:12px;line-height:1.6;color:#98a2b3;margin-top:8px;\">最新一期入口：{latest_url.rstrip('/') + '/'}</div>"
        if latest_url
        else ""
    )
    archive_note = (
        f"<div style=\"font-size:12px;line-height:1.6;color:#98a2b3;margin-top:4px;\">往期历史推荐：{archive_url.rstrip('/') + '/'}</div>"
        if archive_url
        else ""
    )
    Path(output_path).write_text(
        f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f5f7fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#101828;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f7fb;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:720px;background:#ffffff;border-radius:18px;padding:30px;border:1px solid #e4e7ec;">
        <tr><td>
          <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#475467;margin-bottom:10px;">Paper Radar · Weekly Digest</div>
          <div style="font-size:28px;line-height:1.2;font-weight:650;color:#101828;margin-bottom:12px;">具身感知论文雷达已更新</div>
          <div style="font-size:15px;line-height:1.75;color:#344054;margin-bottom:22px;">
            本期精选 10 篇论文，全部带关键图。点击下方按钮打开在线交互网页；这是本期永久链接，不会被下一期覆盖。
          </div>
          <a href="{public_url}" style="display:inline-block;background:#0f766e;color:#ffffff;text-decoration:none;padding:13px 18px;border-radius:999px;font-size:15px;font-weight:650;">打开本期 Paper Radar</a>
          <div style="font-size:13px;line-height:1.6;color:#667085;margin-top:18px;">{public_url}</div>
          {latest_note}
          {archive_note}
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
