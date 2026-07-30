import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

from PIL import Image

from .http import get_bytes
from .image_quality import is_readable_image
from .models import Paper


def materialize_publisher_key_figure(
    paper: Paper,
    output_dir: Path,
    stem: str,
    figure_number: int = 1,
) -> Optional[Path]:
    """Fetch an official publisher-hosted key figure when PDF extraction fails.

    This deliberately uses public media assets exposed by the publisher and does
    not attempt to bypass login, paywall, or anti-bot flows. It is primarily for
    formal journal articles where a PDF is absent/unusable but the article's
    HTML image assets are public.
    """

    for image_url in publisher_figure_candidates(paper, figure_number):
        image_path = _download_image(image_url, output_dir / f"{stem}_publisher_fig{figure_number}.png")
        if image_path:
            paper.key_figure_path = str(image_path)
            paper.key_figure_caption = f"Fig. {figure_number}. Publisher-hosted key figure"
            return image_path
    return None


def publisher_figure_candidates(paper: Paper, figure_number: int = 1) -> Iterable[str]:
    doi = _paper_doi(paper)
    if not doi:
        return []
    lowered_venue = (paper.venue or "").lower()
    if doi.startswith("10.1038/") or "nature" in lowered_venue:
        return _springer_nature_figure_candidates(doi, paper.published_at.year, figure_number)
    return []


def _paper_doi(paper: Paper) -> str:
    if paper.doi:
        return paper.doi.lower()
    if paper.source == "crossref" and paper.source_id:
        return paper.source_id.lower()
    match = re.search(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+", paper.url or "")
    return match.group(0).lower() if match else ""


def _springer_nature_figure_candidates(doi: str, published_year: int, figure_number: int) -> list[str]:
    suffix = doi.split("/", 1)[1]
    # Nature-family article identifiers end in either a numeric checksum or a
    # letter (for example, ``s42256-025-00988-x``).  Springer MediaObjects
    # names use the article number without its zero padding.
    match = re.fullmatch(r"s(\d+)-\d{3}-(\d+)-[0-9a-z]+", suffix)
    if not match:
        return []
    journal_code, article_number = match.groups()
    article_number = str(int(article_number))
    media_name = f"{journal_code}_{published_year}_{article_number}_Fig{figure_number}_HTML.png"
    article_path = quote(f"art:{doi}", safe="")
    base = f"https://media.springernature.com"
    media_path = f"springer-static/image/{article_path}/MediaObjects/{media_name}"
    # lw685 is usually enough for a readable digest and avoids enormous images;
    # full is kept as a fallback for future articles where scaled renditions lag.
    return [
        f"{base}/lw685/{media_path}",
        f"{base}/full/{media_path}",
    ]


def _download_image(url: str, output_path: Path) -> Optional[Path]:
    try:
        data = get_bytes(
            url,
            headers={"User-Agent": "paper-radar/0.1"},
            timeout=8,
            retries=1,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
        with Image.open(output_path) as image:
            # Only verify that the official response is a decodable image. The
            # Figure 1 caption/source identity gate decides suitability.
            if not is_readable_image(image):
                output_path.unlink(missing_ok=True)
                return None
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(output_path)
        return output_path
    except Exception:
        output_path.unlink(missing_ok=True)
        return None
