import html
import json
import re
from datetime import date
from typing import Dict, List
from urllib.parse import urlencode

from ..http import get_bytes
from ..models import Paper


BASE_URL = "https://api.crossref.org/works"


def fetch_work_by_doi(doi: str, mailto: str = "") -> Paper:
    params = {}
    if mailto:
        params["mailto"] = mailto
    suffix = f"?{urlencode(params)}" if params else ""
    url = f"{BASE_URL}/{doi}{suffix}"
    payload = json.loads(
        get_bytes(url, headers={"User-Agent": "paper-radar/0.1"}).decode("utf-8")
    )
    return normalize_work(payload.get("message", {}))


def fetch_recent_journal_works(
    journal_title: str,
    from_date: date,
    rows: int = 100,
    mailto: str = "",
    until_date: date | None = None,
) -> List[Paper]:
    params = {
        "filter": _date_filter(from_date, until_date, f"container-title:{journal_title}"),
        "rows": rows,
        "sort": "published",
        "order": "desc",
    }
    if mailto:
        params["mailto"] = mailto
    url = f"{BASE_URL}?{urlencode(params)}"
    payload = json.loads(
        get_bytes(url, headers={"User-Agent": "paper-radar/0.1"}).decode("utf-8")
    )
    return [normalize_work(item) for item in payload.get("message", {}).get("items", []) if _published_date(item)]


def fetch_recent_query(
    query: str,
    from_date: date,
    rows: int = 100,
    mailto: str = "",
    until_date: date | None = None,
) -> List[Paper]:
    params = {
        "query.bibliographic": query,
        "filter": _date_filter(from_date, until_date),
        "rows": rows,
        "sort": "published",
        "order": "desc",
    }
    if mailto:
        params["mailto"] = mailto
    url = f"{BASE_URL}?{urlencode(params)}"
    payload = json.loads(
        get_bytes(url, headers={"User-Agent": "paper-radar/0.1"}).decode("utf-8")
    )
    return [normalize_work(item) for item in payload.get("message", {}).get("items", []) if _published_date(item)]


def fetch_recent_journal_query(
    journal_title: str,
    query: str,
    from_date: date,
    rows: int = 100,
    mailto: str = "",
    until_date: date | None = None,
) -> List[Paper]:
    params = {
        "query.bibliographic": query,
        "filter": _date_filter(from_date, until_date, f"container-title:{journal_title}"),
        "rows": rows,
        "sort": "published",
        "order": "desc",
    }
    if mailto:
        params["mailto"] = mailto
    url = f"{BASE_URL}?{urlencode(params)}"
    payload = json.loads(
        get_bytes(url, headers={"User-Agent": "paper-radar/0.1"}).decode("utf-8")
    )
    return [normalize_work(item) for item in payload.get("message", {}).get("items", []) if _published_date(item)]


def normalize_work(item: Dict) -> Paper:
    title = " ".join(item.get("title") or [])
    venue = " ".join(item.get("container-title") or [])
    authors = [
        " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part).strip()
        for author in item.get("author", [])
    ]
    return Paper(
        source="crossref",
        source_id=item.get("DOI", ""),
        title=_clean_text(title),
        abstract=_clean_text(item.get("abstract", "")),
        authors=[author for author in authors if author],
        venue=venue,
        published_at=_published_date(item),
        doi=item.get("DOI"),
        url=item.get("URL", ""),
        pdf_url=_best_pdf_url(item),
        raw=item,
    )


def _published_date(item: Dict) -> date:
    for key in ("published-online", "published-print", "published"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0]:
            year, month, day = (parts[0] + [1, 1])[:3]
            return date(year, month, day)
    return None


def _date_filter(from_date: date, until_date: date | None, *extra: str) -> str:
    filters = [f"from-pub-date:{from_date.isoformat()}"]
    if until_date:
        filters.append(f"until-pub-date:{until_date.isoformat()}")
    filters.extend(value for value in extra if value)
    return ",".join(filters)


def _clean_text(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html.unescape(no_tags).split())


def _best_pdf_url(item: Dict) -> str:
    """Pick an official PDF URL from Crossref links when the publisher exposes one."""

    for link in item.get("link", []) or []:
        url = link.get("URL", "")
        content_type = (link.get("content-type") or "").lower()
        if not url:
            continue
        if "pdf" in content_type or "/doi/pdf/" in url or url.lower().endswith(".pdf"):
            return url
    return ""
