import re
from pathlib import Path
from typing import Dict, Iterable, Optional

from PIL import Image, ImageChops

from .fulltext import extract_text_from_pdf, fetch_pdf_bytes
from .image_quality import is_readable_image
from .models import Paper, ScoredPaper
from .publisher_assets import materialize_publisher_key_figure
from .publisher_assets import publisher_figure_candidates


def choose_key_figure_caption(fulltext: str) -> Optional[str]:
    if not fulltext:
        return None
    # Captions are normally emitted at the beginning of a PDF text line.  The
    # label itself may be ``Fig 1``, ``Fig. 1`` or ``Figure 1`` and publishers
    # do not consistently keep a colon/period separator in extracted text.
    # Accept all those label forms, but never promote an inline body reference
    # to a caption merely because it contains "Fig. 1".
    candidates = re.findall(
        r"(?:^|\n)\s*((?:Fig\.?|Figure)\s*\d+[A-Za-z]?(?:\s*[:\.|])?[^\n]{0,260})",
        fulltext,
        flags=re.IGNORECASE,
    )
    if not candidates:
        return None
    fig1_candidates = [
        candidate
        for candidate in candidates
        if re.search(r"(?:Fig\.|Figure)\s*1(?:[A-Za-z])?\b", candidate, flags=re.IGNORECASE)
    ]
    if fig1_candidates:
        # Figure identity is a hard gate.  Inline references and heuristic
        # "overview" matches are not evidence that an image is Figure 1.
        explicit = [
            candidate
            for candidate in fig1_candidates
            if is_verified_figure_one_caption(candidate)
        ]
        if explicit:
            # Publisher HTML commonly uses ``|`` and article PDFs commonly use
            # ``:`` or ``.``. Prefer those explicit caption separators over a
            # line-broken body reference such as ``Figure 1C illustrates``.
            strong = [item for item in explicit if _has_explicit_figure_separator(item)]
            return (strong or explicit)[0].strip()
    return None


def _has_explicit_figure_separator(caption: str) -> bool:
    return bool(
        re.match(
            r"\s*(?:Fig\.?|Figure)\s*1[A-Za-z]?\s*[:.|]",
            caption or "",
            flags=re.IGNORECASE,
        )
    )


def _looks_like_inline_figure_reference(caption: str) -> bool:
    """Detect a body sentence that was line-broken before its Figure 1 token."""

    remainder = re.sub(
        r"^\s*(?:Fig\.?|Figure)\s*1[A-Za-z]?\s*[:.|]?\s*",
        "",
        caption or "",
        flags=re.IGNORECASE,
    )
    return bool(
        re.match(
            r"(?:illustrates?|shows?|demonstrates?|depicts?|compares?|presents?|is|are|was|were|can|may)\b",
            remainder,
            flags=re.IGNORECASE,
        )
    )


def is_verified_figure_one_caption(caption: str) -> bool:
    """Accept an explicit Figure 1 label from a successful caption extraction.

    This deliberately does not impose a punctuation or image-aesthetic rule.
    ``choose_key_figure_caption`` establishes the caption line; this helper
    only confirms that the extracted asset is for Figure 1 rather than another
    numbered figure.
    """

    return bool(
        re.match(
            r"\s*(?:Fig\.?|Figure)\s*1(?:[A-Za-z])?\b",
            caption or "",
            flags=re.IGNORECASE,
        )
    )


def figure_one_audit(paper: Paper) -> Dict[str, str | bool]:
    caption = paper.key_figure_caption or ""
    path = Path(paper.key_figure_path) if paper.key_figure_path else None
    accepted = bool(path and path.is_file() and is_verified_figure_one_caption(caption) and is_readable_image(path))
    return {
        "paper_id": f"{paper.source}:{paper.source_id}",
        "accepted": accepted,
        "caption": caption,
        "figure_path": str(path) if path else "",
        "reason": "verified_figure_one" if accepted else "missing_or_unverified_figure_one",
    }


def materialize_key_figures(
    scored_papers: Iterable[ScoredPaper],
    paper_id_fn,
    output_dir: str = "output/figures",
) -> Dict[str, Dict[str, str]]:
    out = {}
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    for item in scored_papers:
        paper = item.paper
        paper_id = paper_id_fn(paper)
        caption = choose_key_figure_caption(paper.fulltext)
        cached = _existing_key_figure(path, _safe_id(paper_id))
        cached_body_reference = (
            caption
            and _has_explicit_figure_separator(caption)
            and _looks_like_inline_figure_reference(paper.key_figure_caption)
        )
        if cached and is_verified_figure_one_caption(paper.key_figure_caption) and not cached_body_reference:
            paper.key_figure_path = str(cached)
            paper.key_figure_caption = paper.key_figure_caption or "Cached key figure"
            out[paper_id] = {
                "caption": paper.key_figure_caption,
                "figure_number": "",
                "image_path": str(cached),
            }
            continue
        fig_num = _extract_figure_number(caption or "") or "1"
        image_path = None
        prefer_publisher_assets = _prefer_publisher_assets(paper, int(fig_num))
        if prefer_publisher_assets:
            image_path = materialize_publisher_key_figure(
                paper,
                output_dir=path,
                stem=_safe_id(paper_id),
                figure_number=int(fig_num),
            )
        if not image_path and (paper.pdf_url or paper.source != "arxiv"):
            pdf_bytes = fetch_pdf_bytes(paper)
            if pdf_bytes:
                extracted_text = ""
                if not caption:
                    try:
                        extracted_text = extract_text_from_pdf(pdf_bytes)
                        if extracted_text and not paper.fulltext:
                            paper.fulltext = extracted_text
                        caption = choose_key_figure_caption(extracted_text)
                        fig_num = _extract_figure_number(caption or "") or fig_num
                    except Exception:
                        caption = None
                if caption:
                    image_path = crop_figure_by_caption(
                        pdf_bytes,
                        caption=caption,
                        output_dir=path,
                        stem=_safe_id(paper_id),
                    )
        if not image_path and not prefer_publisher_assets:
            image_path = materialize_publisher_key_figure(
                paper,
                output_dir=path,
                stem=_safe_id(paper_id),
                figure_number=int(fig_num),
            )
        if not image_path:
            image_path = materialize_arxiv_companion_key_figure(
                paper,
                output_dir=path,
                stem=_safe_id(paper_id),
            )
        if not image_path:
            continue
        if not is_readable_image(image_path):
            Path(image_path).unlink(missing_ok=True)
            continue
        paper.key_figure_path = str(image_path)
        # A cached crop that originated from a line-broken body reference has
        # just been replaced with a real, explicitly separated Figure 1
        # caption. Do not retain that stale body text in the audit record.
        paper.key_figure_caption = (
            caption
            if cached_body_reference and caption
            else paper.key_figure_caption or caption or f"Fig. {fig_num} | Publisher-hosted Figure 1"
        )
        if not is_verified_figure_one_caption(paper.key_figure_caption):
            paper.key_figure_path = ""
            paper.key_figure_caption = ""
            continue
        out[paper_id] = {
            "caption": paper.key_figure_caption,
            "figure_number": fig_num or "",
            "image_path": str(image_path),
        }
    return out


def materialize_arxiv_companion_key_figure(
    paper: Paper,
    output_dir: Path,
    stem: str,
) -> Optional[Path]:
    """Use a same-title arXiv companion PDF when the formal publisher blocks figures.

    Science/AAAS PDFs commonly resolve through HTML or access-control pages in
    automation environments. If the same paper is also on arXiv, we keep the
    formal journal record but recover its visual completeness from the public
    preprint PDF.
    """

    if paper.source == "arxiv" or not paper.title or not _should_try_arxiv_companion(paper):
        return None

    try:
        from .sources import arxiv
    except Exception:
        return None

    for query in _arxiv_companion_queries(paper.title):
        try:
            candidates = arxiv.fetch_recent(query, max_results=5)
        except Exception:
            continue
        for candidate in candidates:
            if not _titles_equivalent(paper.title, candidate.title):
                continue
            try:
                pdf_bytes = fetch_pdf_bytes(candidate)
            except Exception:
                pdf_bytes = None
            if not pdf_bytes:
                continue
            caption = ""
            try:
                caption = choose_key_figure_caption(extract_text_from_pdf(pdf_bytes, max_pages=6)) or ""
            except Exception:
                caption = ""
            image_path = None
            if caption:
                image_path = crop_figure_by_caption(
                    pdf_bytes,
                    caption=caption,
                    output_dir=output_dir,
                    stem=f"{stem}_arxiv",
                )
            if image_path and is_readable_image(image_path):
                paper.key_figure_caption = caption
                return image_path
    return None


def _existing_key_figure(output_dir: Path, stem: str) -> Optional[Path]:
    for candidate in sorted(output_dir.glob(f"{stem}_*.png")):
        if candidate.is_file() and is_readable_image(candidate):
            return candidate
        if candidate.is_file():
            candidate.unlink(missing_ok=True)
    return None


def _extract_figure_number(caption: str) -> Optional[str]:
    match = re.search(r"(?:Fig\.|Figure)\s*(\d+)", caption, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _safe_id(paper_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", paper_id)


def _arxiv_companion_queries(title: str) -> list[str]:
    clean = " ".join((title or "").split())
    words = re.findall(r"[A-Za-z0-9]+", clean)
    significant = [word for word in words if len(word) > 3][:8]
    queries = [f'"{clean}"']
    if significant:
        queries.append(" ".join(significant))
    return queries


def _prefer_publisher_assets(paper: Paper, figure_number: int) -> bool:
    if paper.source == "arxiv":
        return False
    return any(True for _ in publisher_figure_candidates(paper, figure_number))


def _should_try_arxiv_companion(paper: Paper) -> bool:
    venue = (paper.venue or "").lower()
    doi = (paper.doi or paper.source_id or "").lower()
    url_blob = f"{paper.url or ''} {paper.pdf_url or ''}".lower()
    return "science robotics" in venue or "scirobotics" in doi or "scirobotics" in url_blob


def _titles_equivalent(left: str, right: str) -> bool:
    left_norm = _normalize_title(left)
    right_norm = _normalize_title(right)
    return bool(left_norm) and (left_norm == right_norm or left_norm in right_norm or right_norm in left_norm)


def _normalize_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def crop_figure_by_caption(
    pdf_bytes: bytes,
    caption: str,
    output_dir: Path,
    stem: str,
    dpi_scale: float = 2.4,
) -> Optional[Path]:
    """Render the page containing the caption and crop the figure block above it.

    This is intentionally conservative: it finds the full verified Figure 1
    caption, renders the page region from the top margin through that caption,
    and then trims outer whitespace.  A bare ``Fig. 1`` body reference is never
    enough to authorize a crop.
    """

    try:
        import fitz  # type: ignore
    except Exception:
        return _crop_figure_by_caption_pdfium(
            pdf_bytes,
            caption=caption,
            output_dir=output_dir,
            stem=stem,
            dpi_scale=dpi_scale,
        )

    if not is_verified_figure_one_caption(caption):
        return None
    caption_terms = _caption_search_terms(caption)
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        caption_rect = None
        for term in caption_terms:
            rects = page.search_for(term)
            if rects:
                caption_rect = rects[0]
                break
        if caption_rect is None:
            continue

        page_rect = page.rect
        left = page_rect.x0 + 18
        right = page_rect.x1 - 18
        top = page_rect.y0 + 18

        # Many arXiv/IEEE papers put Fig. 1 in the right column of page 1.
        # In that case, cropping the full page would include title/abstract.
        # For later pages, figures are often full-width, so we keep the wider crop.
        if page_idx == 0 and caption_rect.x0 > page_rect.width * 0.45:
            left = page_rect.x0 + page_rect.width * 0.50
            top = max(page_rect.y0 + 18, caption_rect.y0 - 290)

        clip = fitz.Rect(
            left,
            top,
            right,
            min(page_rect.y1 - 18, caption_rect.y1 + 58),
        )
        if clip.height < 120 or clip.width < 120:
            continue

        pix = page.get_pixmap(matrix=fitz.Matrix(dpi_scale, dpi_scale), clip=clip, alpha=False)
        out_path = output_dir / f"{stem}_caption_p{page_idx + 1}.png"
        pix.save(str(out_path))
        _trim_whitespace(out_path)
        if not is_readable_image(out_path):
            out_path.unlink(missing_ok=True)
            continue
        return out_path
    return None


def _crop_figure_by_caption_pdfium(
    pdf_bytes: bytes,
    caption: str,
    output_dir: Path,
    stem: str,
    dpi_scale: float,
) -> Optional[Path]:
    """Render vector-only figures when PyMuPDF is unavailable.

    PDFium exposes the caption text rectangle in PDF coordinates. The key
    figure is conservatively cropped from the page top through that caption,
    then the existing whitespace/noise cleanup removes page margins.
    """

    try:
        import pypdfium2 as pdfium  # type: ignore

        doc = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return None
    if not is_verified_figure_one_caption(caption):
        return None
    search_terms = _caption_search_terms(caption)
    try:
        match = None
        # Search only phrases derived from an already verified Figure 1
        # caption.  A bare Figure label could be an inline body reference and
        # therefore must never authorize a crop on its own.
        for term in search_terms:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                textpage = page.get_textpage()
                hit = textpage.search(term).get_next()
                if hit:
                    start, count = hit
                    caption_box = _pdfium_text_bounds(textpage, start, count)
                    if caption_box:
                        match = (page_idx, page, caption_box)
                    break
            if match:
                break
        if match:
            page_idx, page, caption_box = match
            _, bottom, _, _ = caption_box
            page_width, page_height = page.get_size()
            image = page.render(scale=dpi_scale).to_pil().convert("RGB")
            left_px = int(18 * dpi_scale)
            right_px = int((page_width - 18) * dpi_scale)
            caption_bottom_px = int((page_height - bottom + 42) * dpi_scale)
            crop = image.crop((left_px, int(18 * dpi_scale), right_px, min(image.height, caption_bottom_px)))
            out_path = output_dir / f"{stem}_caption_p{page_idx + 1}.png"
            crop.save(out_path)
            _trim_whitespace(out_path)
            if is_readable_image(out_path):
                return out_path
            out_path.unlink(missing_ok=True)
    except Exception:
        return None
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return None


def _caption_search_terms(caption: str) -> list[str]:
    """Return non-label text anchors from a verified Figure 1 caption.

    PDF text engines frequently disagree about line wrapping or whitespace in
    a caption, so an exact full-caption search is brittle.  These bounded
    terms stay anchored to the extracted caption's descriptive text rather
    than falling back to a bare ``Fig. 1`` body reference.
    """

    normalized = " ".join((caption or "").split())
    if not is_verified_figure_one_caption(normalized):
        return []
    body = re.sub(
        r"^\s*(?:Fig\.?|Figure)\s*1[A-Za-z]?\s*[:.|]?\s*",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    # The first sentence normally starts on the caption line beside the
    # Figure 1 label.  It remains searchable even when following sentences
    # are rewrapped differently by the PDF engine.
    first_sentence = re.split(r"[.!?]", body, maxsplit=1)[0].strip()
    candidates = [
        first_sentence[:100],
        body[:80],
        body[:56],
        normalized.rstrip(".")[:120],
    ]
    terms: list[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if len(candidate) >= 16 and candidate not in terms:
            terms.append(candidate)
    return terms


def _pdfium_text_bounds(textpage, start: int, count: int) -> Optional[tuple[float, float, float, float]]:
    """Return the PDF-coordinate bounds for a PDFium search result."""

    boxes = []
    for index in range(start, start + count):
        try:
            left, bottom, right, top = textpage.get_charbox(index)
        except Exception:
            continue
        if right > left and top > bottom:
            boxes.append((left, bottom, right, top))
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _trim_whitespace(path: Path) -> None:
    image = Image.open(path).convert("RGB")
    # Treat near-white page background as removable border.
    bg = Image.new("RGB", image.size, (255, 255, 255))
    diff = Image.eval(ImageChops.difference(image, bg), lambda px: 255 if px > 12 else 0)
    bbox = diff.getbbox()
    if not bbox:
        return
    left, top, right, bottom = bbox
    pad = 18
    crop = (
        max(0, left - pad),
        max(0, top - pad),
        min(image.width, right + pad),
        min(image.height, bottom + pad),
    )
    cropped = image.crop(crop)
    cropped = _trim_textual_noise(cropped)
    cropped.save(path)


def _trim_textual_noise(image: Image.Image) -> Image.Image:
    """Remove obvious article title/header bands around figure crops.

    This is intentionally heuristic. It trims sparse text-only bands from the
    top/bottom while preserving dense graphical regions. It is not meant to be a
    semantic detector; it just makes caption-based crops feel less like raw PDF
    screenshots.
    """

    gray = image.convert("L")
    width, height = gray.size
    pixels = gray.load()

    def row_dark_ratio(y: int) -> float:
        dark = 0
        for x in range(width):
            if pixels[x, y] < 235:
                dark += 1
        return dark / max(1, width)

    top = 0
    # Skip mostly-white margin.
    while top < height * 0.35 and row_dark_ratio(top) < 0.01:
        top += 1

    # If the top region consists of sparse text rows before a large graphic,
    # advance until we hit denser content.
    dense_seen = False
    probe = top
    while probe < height * 0.45:
        ratio = row_dark_ratio(probe)
        if ratio > 0.18:
            dense_seen = True
            top = max(0, probe - 12)
            break
        probe += 1
    if not dense_seen:
        top = max(0, top - 8)

    bottom = height - 1
    while bottom > height * 0.55 and row_dark_ratio(bottom) < 0.01:
        bottom -= 1

    if bottom - top < height * 0.4:
        return image
    return image.crop((0, top, width, bottom + 1))
