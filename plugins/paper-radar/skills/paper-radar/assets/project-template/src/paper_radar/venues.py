def matches_preferred_venue(venue: str, preferred_venues) -> bool:
    normalized_venue = _normalize_venue(venue)
    venue_tokens = set(normalized_venue.split())
    for preferred in preferred_venues:
        normalized_preferred = _normalize_venue(preferred)
        if not normalized_preferred:
            continue
        if normalized_preferred in {"nature", "science", "cell"}:
            if normalized_venue == normalized_preferred:
                return True
            continue
        if _is_short_acronym(normalized_preferred):
            if normalized_preferred in venue_tokens:
                return True
            continue
        if normalized_preferred in normalized_venue:
            return True
    return False


def _normalize_venue(value: str) -> str:
    return " ".join((value or "").lower().replace("&", "and").split())


def _is_short_acronym(value: str) -> bool:
    return value.isalnum() and len(value) <= 5
