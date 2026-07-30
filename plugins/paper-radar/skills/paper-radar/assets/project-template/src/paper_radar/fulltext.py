from io import BytesIO
from typing import Optional

from pypdf import PdfReader

from .http import get_bytes
from .models import Paper
from .scansci_recovery import recover_pdf_bytes


def fetch_fulltext(paper: Paper) -> Optional[str]:
    try:
        pdf_bytes = fetch_pdf_bytes(paper)
        if not pdf_bytes:
            return None
        return extract_text_from_pdf(pdf_bytes)
    except Exception:
        return None


def fetch_pdf_bytes(paper: Paper) -> Optional[bytes]:
    if paper.pdf_url:
        try:
            pdf_bytes = get_bytes(paper.pdf_url, headers={"User-Agent": "paper-radar/0.1"}, timeout=8, retries=1)
        except Exception:
            pdf_bytes = None
        if _looks_like_pdf(pdf_bytes):
            return pdf_bytes
    return recover_pdf_bytes(paper)


def extract_text_from_pdf(pdf_bytes: bytes, max_pages: int = 20) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        if text:
            pages.append(text)
    return "\n".join(pages)


def extract_abstract_from_fulltext(fulltext: str) -> str:
    """Recover a missing abstract from a publisher PDF text layer."""

    if not fulltext:
        return ""
    import re

    match = re.search(
        r"(?:^|\n)\s*Abstract\s*\n?(.*?)(?=\n\s*(?:Introduction|Keywords?|Main)\b)",
        fulltext,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    abstract = re.sub(r"\s+", " ", match.group(1)).strip()
    return abstract if 120 <= len(abstract) <= 5000 else ""


def _looks_like_pdf(payload: bytes) -> bool:
    if not payload:
        return False
    return payload.lstrip().startswith(b"%PDF")
