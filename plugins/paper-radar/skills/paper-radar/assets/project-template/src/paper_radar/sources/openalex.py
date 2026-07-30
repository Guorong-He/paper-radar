import json
from datetime import date
from typing import Dict, Iterable, List
from urllib.parse import urlencode

from ..http import get_bytes
from ..models import Paper


BASE_URL = "https://api.openalex.org/works"


def fetch_recent(
    query: str,
    from_date: date,
    per_page: int = 25,
    mailto: str = "",
    until_date: date | None = None,
) -> List[Paper]:
    date_filters = [f"from_publication_date:{from_date.isoformat()}"]
    if until_date:
        date_filters.append(f"to_publication_date:{until_date.isoformat()}")
    params = {
        "search": query,
        "filter": ",".join(date_filters),
        "per-page": per_page,
    }
    if mailto:
        params["mailto"] = mailto
    url = f"{BASE_URL}?{urlencode(params)}"
    payload = json.loads(
        get_bytes(url, headers={"User-Agent": "paper-radar/0.1"}).decode("utf-8")
    )
    return [normalize_work(item) for item in payload.get("results", []) if item.get("publication_date")]


def normalize_work(item: Dict) -> Paper:
    abstract = inverted_index_to_text(item.get("abstract_inverted_index") or {})
    authors = [
        authorship.get("author", {}).get("display_name", "")
        for authorship in item.get("authorships", [])
        if authorship.get("author", {}).get("display_name")
    ]
    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    venue = source.get("display_name") or ""
    best_oa_location = item.get("best_oa_location") or {}
    return Paper(
        source="openalex",
        source_id=item["id"].rsplit("/", 1)[-1],
        title=item.get("display_name") or "",
        abstract=abstract,
        authors=authors,
        venue=venue,
        published_at=date.fromisoformat(item["publication_date"]),
        doi=item.get("doi"),
        url=item.get("id") or "",
        pdf_url=best_oa_location.get("pdf_url") or "",
        raw=item,
    )


def inverted_index_to_text(index: Dict[str, Iterable[int]]) -> str:
    positions = []
    for word, locs in index.items():
        for loc in locs:
            positions.append((loc, word))
    positions.sort()
    return " ".join(word for _, word in positions)
