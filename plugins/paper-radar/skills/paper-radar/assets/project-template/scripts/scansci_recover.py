#!/usr/bin/env python3
"""Paper Radar's approved-source ScanSci PDF recovery runner."""

import argparse
import json
import os
import re
import shutil
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("doi")
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    from scansci_pdf.config import load_config
    from scansci_pdf.publisher_strategies import try_elsevier_api
    from scansci_pdf.sources.core_api import try_core
    from scansci_pdf.sources.crossref import try_crossref_page_scrape
    from scansci_pdf.sources.europepmc import try_europepmc, try_pmc
    from scansci_pdf.sources.nature import try_nature_direct
    from scansci_pdf.sources.oa_discovery import try_doaj
    from scansci_pdf.sources.openalex import try_openalex_content_api
    from scansci_pdf.sources.semantic_scholar import try_semanticscholar
    from scansci_pdf.sources.unpaywall import try_unpaywall
    from scansci_pdf.network import fetch_json, polite_delay
    from scansci_pdf.pdf_utils import download_pdf

    config = load_config()
    config.update(
        {
            "scihub_enabled": False,
            "scihub_domains": [],
            "tor_proxy": "",
            "use_tor_for_scihub": False,
            "connect_timeout": 6,
            "read_timeout": 12,
            "request_delay_min": 0.3,
            "request_delay_max": 0.8,
            "parallel_sources": False,
            "parallel_probes": False,
        }
    )
    config["elsevier_api_key"] = os.getenv("ELSEVIER_API_KEY") or config.get("elsevier_api_key", "")
    config["elsevier_insttoken"] = os.getenv("ELSEVIER_INSTTOKEN") or config.get("elsevier_insttoken", "")
    config["openalex_api_key"] = os.getenv("OPENALEX_API_KEY") or config.get("openalex_api_key", "")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    provenance = output.with_suffix(".json")
    provenance.unlink(missing_ok=True)

    priority_sources = []
    if args.doi.startswith("10.1016/") and config.get("elsevier_api_key"):
        priority_sources.append(("ElsevierAPI", try_elsevier_api))
    if args.doi.startswith("10.1038/"):
        priority_sources.append(("NatureDirect", try_nature_direct))
    if config.get("openalex_api_key"):
        priority_sources.append(("OpenAlexContent", try_openalex_content_api))

    # User-authorized institutional routes belong ahead of weak discovery
    # fallbacks so a valid signed-in session is actually useful within the
    # per-paper timeout.
    if config.get("instsci_enabled"):
        try:
            from scansci_pdf.sources.instsci import try_instsci

            priority_sources.append(("InstSci", try_instsci))
        except ImportError:
            pass
    if config.get("carsi_enabled") and config.get("carsi_idp_name"):
        try:
            from scansci_pdf.sources.carsi_source import try_carsi

            priority_sources.append(("CARSI", try_carsi))
        except ImportError:
            pass
    if config.get("ezproxy_enabled") and config.get("ezproxy_login_url"):
        try:
            from scansci_pdf.sources.ezproxy import try_ezproxy

            priority_sources.append(("EZProxy", try_ezproxy))
        except ImportError:
            pass

    # The formal publisher route has already been tried. These are lawful
    # public versions of that same work: an author arXiv manuscript, a public
    # author manuscript, or a repository deposit. The DOI is the primary
    # identity anchor; title fallback only discovers an arXiv version that has
    # not yet been linked to the journal DOI.
    public_sources = [
        ("InstitutionalRepository", lambda doi, path, cfg: try_openalex_repository(doi, args.title, path, cfg, fetch_json, polite_delay, download_pdf)),
        ("AuthorPublicManuscript", lambda doi, path, cfg: try_unpaywall_manuscript(doi, path, cfg, try_unpaywall)),
        ("AuthorArXiv", lambda doi, path, cfg: try_semantic_or_arxiv(doi, args.title, path, cfg, try_semanticscholar, fetch_json, polite_delay, download_pdf)),
        ("PMC", try_pmc),
        ("EuropePMC", try_europepmc),
        ("DOAJ", try_doaj),
        ("CORE", try_core),
        ("CrossrefPage", try_crossref_page_scrape),
    ]

    def publish_success(label: str, candidate: Path) -> int:
        if candidate != output:
            shutil.copy2(candidate, output)
        record = {
            "doi": args.doi,
            "source": label,
            "recovered_at": datetime.now(timezone.utc).isoformat(),
        }
        provenance.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(output, 0o600)
        os.chmod(provenance, 0o600)
        print("SCANSCI_RESULT=" + json.dumps({"success": True, "file": str(output), "source": label}))
        return 0

    attempts = []
    for label, source_fn in priority_sources:
        output.unlink(missing_ok=True)
        try:
            result = source_fn(args.doi, output, config)
        except Exception as exc:
            attempts.append({"source": label, "status": "error", "detail": type(exc).__name__})
            continue
        if result and result.get("success") and output.is_file():
            return publish_success(str(result.get("source") or label), output)
        attempts.append({"source": label, "status": "miss"})

    # All of these sources are explicitly approved and independent. Give each
    # one a separate temporary file so a slow repository cannot consume the
    # entire per-paper budget before the remaining sources are tried.
    results = {}
    with tempfile.TemporaryDirectory(prefix="scansci-approved-", dir=output.parent) as tmp:
        temp_dir = Path(tmp)
        with ThreadPoolExecutor(max_workers=len(public_sources)) as executor:
            futures = {}
            for index, (label, source_fn) in enumerate(public_sources):
                candidate = temp_dir / f"{index:02d}-{label}.pdf"
                future = executor.submit(source_fn, args.doi, candidate, config)
                futures[future] = (index, label, candidate)
            for future in as_completed(futures):
                index, label, candidate = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    results[index] = (label, candidate, None, type(exc).__name__)
                else:
                    results[index] = (label, candidate, result, "")

        for index in range(len(public_sources)):
            label, candidate, result, error = results[index]
            if error:
                attempts.append({"source": label, "status": "error", "detail": error})
                continue
            if result and result.get("success") and candidate.is_file():
                return publish_success(str(result.get("source") or label), candidate)
            attempts.append({"source": label, "status": "miss"})

    output.unlink(missing_ok=True)
    print("SCANSCI_RESULT=" + json.dumps({"success": False, "attempts": attempts}))
    return 2


def try_openalex_repository(
    doi: str,
    expected_title: str,
    output_path: Path,
    config: dict,
    fetch_json,
    polite_delay,
    download_pdf,
):
    """Fetch a green-OA repository PDF for the DOI-identified work only."""

    url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='')}"
    payload = fetch_json(url, config) or {}
    if not _same_work_title(expected_title, payload.get("title", "")):
        return None
    for location in payload.get("locations") or []:
        if not isinstance(location, dict) or location.get("host_type") != "repository":
            continue
        pdf_url = location.get("pdf_url") or ""
        if not pdf_url:
            continue
        polite_delay(config)
        result = download_pdf(pdf_url, output_path, config, "InstitutionalRepository")
        if result:
            result["source"] = "InstitutionalRepository"
            return result
    return None


def try_unpaywall_manuscript(doi: str, output_path: Path, config: dict, try_unpaywall):
    """Use Unpaywall's repository-first public-version discovery."""

    result = try_unpaywall(doi, output_path, config)
    if result:
        result["source"] = "AuthorPublicManuscript"
    return result


def try_semantic_or_arxiv(
    doi: str,
    expected_title: str,
    output_path: Path,
    config: dict,
    try_semanticscholar,
    fetch_json,
    polite_delay,
    download_pdf,
):
    """Try an exact DOI-linked public version, then a title-matched arXiv ID."""

    result = try_semanticscholar(doi, output_path, config)
    if result:
        source = str(result.get("source") or "").lower()
        result["source"] = "AuthorArXiv" if "arxiv" in source else "AuthorPublicManuscript"
        return result
    if not expected_title:
        return None

    search_url = "https://api.semanticscholar.org/graph/v1/paper/search"
    payload = fetch_json(
        f"{search_url}?query={urllib.parse.quote(expected_title)}&limit=5&fields=title,externalIds",
        config,
    ) or {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not _same_work_title(expected_title, item.get("title", "")):
            continue
        arxiv_id = (item.get("externalIds") or {}).get("ArXiv")
        if not arxiv_id:
            continue
        polite_delay(config)
        result = download_pdf(f"https://arxiv.org/pdf/{arxiv_id}.pdf", output_path, config, "AuthorArXiv")
        if result:
            result["source"] = "AuthorArXiv"
            return result
    return try_arxiv_title_api(expected_title, output_path, config, polite_delay, download_pdf)


def try_arxiv_title_api(
    expected_title: str,
    output_path: Path,
    config: dict,
    polite_delay,
    download_pdf,
):
    """Use arXiv's public Atom API for a same-title author manuscript.

    This is a narrow fallback for journal papers whose DOI has not been
    deposited in Semantic Scholar. Both title-overlap thresholds remain in
    force, so a merely similar arXiv paper cannot replace the formal work.
    """

    query = urllib.parse.quote(f'ti:"{expected_title}"')
    request = Request(
        f"https://export.arxiv.org/api/query?search_query={query}&start=0&max_results=10",
        headers={"User-Agent": "paper-radar/0.1"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = response.read()
        root = ET.fromstring(payload)
    except Exception:
        return None

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", namespace):
        title = " ".join((entry.findtext("atom:title", default="", namespaces=namespace) or "").split())
        if not _same_work_title(expected_title, title):
            continue
        identifier = (entry.findtext("atom:id", default="", namespaces=namespace) or "").rstrip("/").split("/")[-1]
        identifier = re.sub(r"v\d+$", "", identifier)
        if not identifier:
            continue
        polite_delay(config)
        result = download_pdf(f"https://arxiv.org/pdf/{identifier}.pdf", output_path, config, "AuthorArXiv")
        if result:
            result["source"] = "AuthorArXiv"
            return result
    return None


def _same_work_title(expected: str, candidate: str) -> bool:
    expected_tokens = set(re.findall(r"[a-z0-9]+", (expected or "").lower()))
    candidate_tokens = set(re.findall(r"[a-z0-9]+", (candidate or "").lower()))
    if not expected_tokens or not candidate_tokens:
        return False
    overlap = len(expected_tokens & candidate_tokens)
    return overlap / len(expected_tokens) >= 0.9 and overlap / len(candidate_tokens) >= 0.8


if __name__ == "__main__":
    raise SystemExit(main())
