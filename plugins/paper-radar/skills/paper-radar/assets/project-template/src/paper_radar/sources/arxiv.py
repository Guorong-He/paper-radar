import os
import time
from datetime import date
from typing import Dict, List
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from ..http import get_bytes
from ..models import Paper


BASE_URL = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_LAST_REQUEST_TS = 0.0


def fetch_recent(query: str, start: int = 0, max_results: int = 25) -> List[Paper]:
    _throttle()
    params = {
        "search_query": f"all:{query}",
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{BASE_URL}?{urlencode(params)}"
    xml_payload = get_bytes(url, headers={"User-Agent": "paper-radar/0.1"})
    root = ET.fromstring(xml_payload)
    return [normalize_entry(entry) for entry in root.findall("atom:entry", NS)]


def normalize_entry(entry: ET.Element) -> Paper:
    source_id = entry.findtext("atom:id", default="", namespaces=NS).rsplit("/", 1)[-1]
    published = entry.findtext("atom:published", default="", namespaces=NS)[:10]
    links = entry.findall("atom:link", NS)
    pdf_url = ""
    for link in links:
        if link.attrib.get("title") == "pdf":
            pdf_url = link.attrib.get("href", "")
            break
    authors = [
        author.findtext("atom:name", default="", namespaces=NS)
        for author in entry.findall("atom:author", NS)
    ]
    return Paper(
        source="arxiv",
        source_id=source_id,
        title=_clean(entry.findtext("atom:title", default="", namespaces=NS)),
        abstract=_clean(entry.findtext("atom:summary", default="", namespaces=NS)),
        authors=authors,
        published_at=date.fromisoformat(published),
        venue="arXiv",
        url=entry.findtext("atom:id", default="", namespaces=NS),
        pdf_url=pdf_url,
        raw={"source_id": source_id},
    )


def _clean(text: str) -> str:
    return " ".join(text.split())


def _throttle() -> None:
    global _LAST_REQUEST_TS
    min_interval = float(os.getenv("PAPER_RADAR_ARXIV_MIN_INTERVAL_SECONDS", "3.0"))
    if min_interval <= 0:
        _LAST_REQUEST_TS = time.monotonic()
        return
    now = time.monotonic()
    elapsed = now - _LAST_REQUEST_TS
    if _LAST_REQUEST_TS and elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _LAST_REQUEST_TS = time.monotonic()
