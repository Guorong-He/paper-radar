import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List

from ..http import get_bytes


RSS_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "rss": "http://purl.org/rss/1.0/",
}


@dataclass(frozen=True)
class TocCandidate:
    doi: str
    title: str
    venue: str
    published_at: date | None
    url: str
    summary: str = ""


def fetch_candidate_dois(
    feeds: Iterable[dict],
    from_date: date,
    relevance_terms: Iterable[str],
    max_items_per_feed: int = 30,
    timeout: int = 10,
) -> List[TocCandidate]:
    """Fetch DOI candidates from publisher feeds.

    This source is intentionally a recall aid only. It does not score papers and
    it does not bypass the normal Crossref normalization, relevance filter,
    history exclusion, tiered selection, or key-figure gate.
    """

    candidates: list[TocCandidate] = []
    seen_dois: set[str] = set()
    terms = [term.lower() for term in relevance_terms if term]
    for feed in feeds:
        url = (feed.get("url") or "").strip()
        if not url:
            continue
        default_venue = feed.get("name") or ""
        try:
            xml_payload = get_bytes(
                url,
                headers={"User-Agent": "paper-radar/0.1"},
                timeout=timeout,
                retries=1,
            )
            feed_candidates = parse_feed(xml_payload, default_venue=default_venue)
        except Exception:
            continue
        for candidate in feed_candidates[:max_items_per_feed]:
            doi = _canonical_doi(candidate.doi)
            if not doi or doi in seen_dois:
                continue
            if candidate.published_at and candidate.published_at < from_date:
                continue
            if not _looks_like_research_doi(doi):
                continue
            if not _matches_relevance(candidate, terms):
                continue
            candidates.append(
                TocCandidate(
                    doi=doi,
                    title=candidate.title,
                    venue=candidate.venue,
                    published_at=candidate.published_at,
                    url=candidate.url,
                    summary=candidate.summary,
                )
            )
            seen_dois.add(doi)
    return candidates


def parse_feed(xml_payload: bytes, default_venue: str = "") -> List[TocCandidate]:
    root = ET.fromstring(xml_payload)
    items = root.findall(".//rss:item", RSS_NS)
    if not items:
        items = root.findall(".//channel/item")
    if not items:
        items = root.findall(".//atom:entry", RSS_NS)
    return [_parse_item(item, default_venue) for item in items]


def _parse_item(item: ET.Element, default_venue: str) -> TocCandidate:
    title = _first_text(
        item,
        [
            "rss:title",
            "title",
            "atom:title",
            "dc:title",
        ],
    )
    url = _first_text(item, ["rss:link", "link", "prism:url", "atom:id"])
    if not url:
        atom_link = item.find("atom:link", RSS_NS)
        url = atom_link.attrib.get("href", "") if atom_link is not None else ""
    summary = _clean_text(
        _first_text(
            item,
            [
                "content:encoded",
                "description",
                "rss:description",
                "atom:summary",
            ],
        )
    )
    doi = _first_text(item, ["prism:doi", "dc:identifier"])
    if not doi:
        doi = _extract_doi(" ".join([title, url, summary]))
    venue = _first_text(item, ["prism:publicationName", "dc:source"]) or default_venue
    published_at = _parse_date(_first_text(item, ["dc:date", "pubDate", "atom:published", "atom:updated"]))
    return TocCandidate(
        doi=_canonical_doi(doi),
        title=_clean_text(title),
        venue=_clean_text(venue),
        published_at=published_at,
        url=url,
        summary=summary,
    )


def _first_text(item: ET.Element, paths: list[str]) -> str:
    for path in paths:
        found = item.find(path, RSS_NS)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _canonical_doi(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    return cleaned


def _extract_doi(text: str) -> str:
    match = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+", text or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return match.group(0).rstrip(".,);]")


def _clean_text(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html.unescape(no_tags).split())


def _looks_like_research_doi(doi: str) -> bool:
    # Nature news/editorial items commonly use d41586 DOIs. They can mention
    # robotics, but they are not primary research candidates for this digest.
    return "/d41586-" not in doi


def _matches_relevance(candidate: TocCandidate, terms: list[str]) -> bool:
    if not terms:
        return True
    blob = " ".join([candidate.title, candidate.summary]).lower()
    return any(term in blob for term in terms)
