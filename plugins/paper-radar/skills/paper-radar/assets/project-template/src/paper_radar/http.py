import time
from typing import Dict, Optional

import requests


MAX_RETRY_WAIT_SECONDS = 120.0


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def get_bytes(url: str, headers: Dict[str, str], timeout: int = 15, retries: int = 3) -> bytes:
    last_exc = None
    session = _new_session()
    try:
        for attempt in range(retries):
            try:
                resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                resp.raise_for_status()
                return resp.content
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    retry_delay = _retry_delay_seconds(exc, attempt)
                    # A very long Retry-After means the source has exhausted a
                    # quota for this run. Respect it by not retrying early, but
                    # do not put the whole weekly workflow to sleep for hours.
                    if retry_delay is None:
                        break
                    time.sleep(retry_delay)
    finally:
        session.close()
    raise last_exc


def probe_url(url: str, headers: Dict[str, str], timeout: int = 8) -> int:
    session = _new_session()
    try:
        resp = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.status_code
    finally:
        session.close()


def _retry_delay_seconds(exc: Exception, attempt: int) -> Optional[float]:
    base_delay = float(2 ** attempt)
    if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After")
        try:
            retry_after_seconds = float(retry_after) if retry_after else 0.0
        except ValueError:
            retry_after_seconds = 0.0
        if retry_after_seconds > MAX_RETRY_WAIT_SECONDS:
            return None
        return max(base_delay, retry_after_seconds, 8.0 * (attempt + 1))
    return base_delay
