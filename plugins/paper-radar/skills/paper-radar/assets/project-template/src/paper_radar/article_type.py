"""Small landing-page metadata gate for formal primary research articles."""

import re
from typing import Any

import requests

from .models import Paper


NON_RESEARCH_TYPES = {
    "news & views",
    "news and views",
    "comment",
    "commentary",
    "editorial",
    "perspective",
    "review",
    "book review",
    "correction",
    "publisher correction",
}
_NATURE_URL = re.compile(r"^https?://(?:www\.)?nature\.com/articles/", re.IGNORECASE)
_NATURE_DOI = re.compile(r"^10\.1038/([a-z0-9.-]+)$", re.IGNORECASE)


def primary_research_audit(paper: Paper) -> dict[str, Any]:
    """Classify only known non-research article types as ineligible.

    This is not full-text reading. For Nature family pages it consumes at most
    64 KiB from the landing-page head and reads the publisher's `contentType`
    metadata. Network uncertainty is deliberately fail-open: quality selection
    must not mistake an unreachable landing page for evidence of an editorial.
    """

    raw_type = _raw_article_type(paper)
    if _is_non_research(raw_type):
        return {"accepted": False, "source": "metadata", "article_type": raw_type}
    landing_url = _nature_landing_url(paper)
    if paper.source == "arxiv" or not landing_url:
        return {"accepted": True, "source": "metadata", "article_type": raw_type}

    landing_type = _nature_landing_content_type(landing_url)
    if _is_non_research(landing_type):
        return {"accepted": False, "source": "publisher_landing_metadata", "article_type": landing_type}
    return {
        "accepted": True,
        "source": "publisher_landing_metadata" if landing_type else "metadata",
        "article_type": landing_type or raw_type,
    }


def _raw_article_type(paper: Paper) -> str:
    raw = paper.raw if isinstance(paper.raw, dict) else {}
    values = [raw.get(key) for key in ("article_type", "article-type", "subtype", "type")]
    return " ".join(str(value or "") for value in values).strip().lower()


def _nature_landing_url(paper: Paper) -> str:
    """Return a publisher landing URL for Nature-family DOIs when available."""

    if _NATURE_URL.match(paper.url or ""):
        return paper.url
    doi = (paper.doi or "").strip().lower()
    match = _NATURE_DOI.match(doi)
    if match:
        return f"https://www.nature.com/articles/{match.group(1)}"
    return ""


def _nature_landing_content_type(url: str) -> str:
    try:
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                url,
                headers={
                    "User-Agent": "paper-radar/0.1",
                    "Range": "bytes=0-65535",
                },
                timeout=10,
                allow_redirects=True,
                stream=True,
            )
            response.raise_for_status()
            chunks: list[bytes] = []
            remaining = 65_536
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk or remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                remaining -= len(chunk)
            head = b"".join(chunks).decode("utf-8", "ignore")
        finally:
            session.close()
    except requests.RequestException:
        return ""
    match = re.search(r'"contentType"\s*:\s*"([^"]+)"', head, flags=re.IGNORECASE)
    return match.group(1).strip().lower() if match else ""


def _is_non_research(article_type: str) -> bool:
    normalized = " ".join((article_type or "").lower().split())
    return any(marker in normalized for marker in NON_RESEARCH_TYPES)
