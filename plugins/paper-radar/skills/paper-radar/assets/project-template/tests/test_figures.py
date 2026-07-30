import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.colors import Color
from reportlab.pdfgen import canvas

from paper_radar import figures
from paper_radar.image_quality import is_readable_image
from paper_radar.models import Paper


def write_valid_figure(path: Path) -> Path:
    """Write a small but content-rich synthetic figure for extraction tests."""

    figure = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(figure)
    for x in range(80, 930, 120):
        draw.line((x, 80, x, 620), fill=(215, 215, 215), width=2)
    for y in range(120, 620, 90):
        draw.line((70, y, 930, y), fill=(215, 215, 215), width=2)
    draw.rectangle((100, 120, 360, 360), fill=(226, 238, 255), outline=(40, 90, 180), width=8)
    draw.ellipse((430, 130, 760, 430), fill=(255, 231, 226), outline=(180, 60, 90), width=8)
    draw.rectangle((115, 455, 360, 610), fill=(232, 245, 232), outline=(50, 120, 70), width=6)
    draw.rectangle((660, 470, 900, 610), fill=(245, 235, 255), outline=(105, 70, 150), width=6)
    draw.line([(110, 600), (250, 470), (390, 520), (540, 310), (720, 380), (900, 180)], fill=(30, 120, 90), width=10)
    for index, x in enumerate(range(150, 860, 90)):
        y = 210 + (index % 4) * 70
        draw.ellipse((x, y, x + 34, y + 34), fill=(35, 95, 190))
        draw.line((x + 17, y + 17, min(930, x + 86), 540 - (index % 3) * 60), fill=(150, 80, 40), width=4)
    draw.text((105, 385), "robot navigation policy", fill=(40, 40, 40))
    draw.text((430, 455), "visual homing vector", fill=(40, 40, 40))
    draw.text((675, 620), "A  B  C  D", fill=(40, 40, 40))
    figure.save(path)
    return path


class FigureTests(unittest.TestCase):
    def test_pdfium_caption_crop_uses_verified_caption_text_not_exact_whitespace(self):
        caption = "Fig. 1.Deployment of the robot policy in a cluttered lab."
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "caption.pdf"
            document = canvas.Canvas(str(source), pagesize=(612, 792))
            document.setFillColor(Color(0.20, 0.42, 0.78))
            document.rect(72, 250, 468, 310, fill=1, stroke=0)
            document.setFillColor(Color(0.85, 0.25, 0.18))
            document.circle(306, 405, 90, fill=1, stroke=0)
            document.setFillColor(Color(0, 0, 0))
            # The PDF's visible caption adds whitespace after the label while
            # the extracted caption deliberately does not, matching a common
            # PDF-text discrepancy this fallback must handle.
            document.drawString(72, 130, "Fig. 1. Deployment of the robot policy in a cluttered lab.")
            document.save()

            output = figures._crop_figure_by_caption_pdfium(
                source.read_bytes(),
                caption=caption,
                output_dir=root,
                stem="caption",
                dpi_scale=2.4,
            )

            self.assertIsNotNone(output)
            self.assertTrue(is_readable_image(output))

    def test_key_figure_caption_accepts_spelled_out_figure_label(self):
        caption = figures.choose_key_figure_caption(
            "The system is illustrated in Fig. 1. Instead of passive fusion.\n"
            "Figure 1: Overview of UniTacVLA and its tactile world model.\n"
            "The remainder of the paper follows."
        )

        self.assertEqual(
            caption,
            "Figure 1: Overview of UniTacVLA and its tactile world model.",
        )
        self.assertEqual(figures._extract_figure_number(caption), "1")

    def test_key_figure_caption_prefers_nature_pipe_caption_over_inline_reference(self):
        caption = figures.choose_key_figure_caption(
            "The device is shown in Fig. 1a.\n"
            "Fig. 1 | Design and function of the wearable bioelectronic device.\n"
        )

        self.assertEqual(
            caption,
            "Fig. 1 | Design and function of the wearable bioelectronic device.",
        )

    def test_key_figure_caption_prefers_period_caption_over_inline_reference(self):
        caption = figures.choose_key_figure_caption(
            "Figure 1C illustrates the sensor architecture used in the experiment.\n"
            "Figure 1. Illustration of the stretchable multimodal deformation sensor.\n"
        )

        self.assertEqual(
            caption,
            "Figure 1. Illustration of the stretchable multimodal deformation sensor.",
        )

    def test_cached_body_reference_is_replaced_by_explicit_figure_one_caption(self):
        paper = Paper(
            source="arxiv",
            source_id="2607.00001v1",
            title="Paper with a line-broken Figure 1 body reference",
            abstract="",
            authors=[],
            venue="arXiv",
            published_at=date(2026, 7, 1),
            pdf_url="https://example.com/paper.pdf",
            fulltext=(
                "Figure 1C illustrates the sensor architecture used in the experiment.\n"
                "Figure 1. Illustration of the stretchable multimodal deformation sensor.\n"
            ),
            key_figure_caption="Figure 1C illustrates the sensor architecture used in the experiment.",
        )
        old_fetch_pdf = figures.fetch_pdf_bytes
        old_crop = figures.crop_figure_by_caption
        try:
            figures.fetch_pdf_bytes = lambda _paper: b"%PDF synthetic"
            captured = []

            def fake_crop(_pdf, caption, output_dir, stem):
                captured.append(caption)
                return write_valid_figure(output_dir / f"{stem}_caption_p8.png")

            figures.crop_figure_by_caption = fake_crop
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                stale = output / "arxiv_2607.00001v1_caption_p5.png"
                write_valid_figure(stale)
                scored = [type("Scored", (), {"paper": paper})()]
                figures.materialize_key_figures(scored, lambda item: f"{item.source}:{item.source_id}", output_dir=output)

            self.assertEqual(captured, ["Figure 1. Illustration of the stretchable multimodal deformation sensor."])
            self.assertTrue(paper.key_figure_path.endswith("caption_p8.png"))
            self.assertEqual(
                paper.key_figure_caption,
                "Figure 1. Illustration of the stretchable multimodal deformation sensor.",
            )
        finally:
            figures.fetch_pdf_bytes = old_fetch_pdf
            figures.crop_figure_by_caption = old_crop

    def test_key_figure_caption_accepts_verified_figure_one_panel_a(self):
        caption = figures.choose_key_figure_caption(
            "Fig. 1A: Robot morphology and tactile sensor placement.\n"
        )

        self.assertEqual(caption, "Fig. 1A: Robot morphology and tactile sensor placement.")
        self.assertTrue(figures.is_verified_figure_one_caption(caption))

    def test_key_figure_caption_accepts_figure_one_without_publisher_punctuation(self):
        caption = figures.choose_key_figure_caption(
            "Figure 1 Robot morphology and tactile sensor placement\n"
        )

        self.assertEqual(caption, "Figure 1 Robot morphology and tactile sensor placement")
        self.assertTrue(figures.is_verified_figure_one_caption(caption))

    def test_formal_paper_without_pdf_url_uses_recovery_fetch(self):
        paper = Paper(
            source="crossref",
            source_id="10.1126/sciadv.example",
            title="Formal paper requiring recovery",
            abstract="",
            authors=[],
            venue="Science Advances",
            published_at=date(2026, 6, 1),
        )

        old_fetch_pdf = figures.fetch_pdf_bytes
        old_extract_text = figures.extract_text_from_pdf
        old_crop = figures.crop_figure_by_caption
        try:
            calls = []
            figures.fetch_pdf_bytes = lambda _paper: calls.append("recover") or b"%PDF fake bytes"
            figures.extract_text_from_pdf = lambda _pdf, max_pages=20: "Fig. 1. System overview. " * 50

            def fake_crop(_pdf, caption, output_dir, stem):
                self.assertTrue(figures.is_verified_figure_one_caption(caption))
                return write_valid_figure(output_dir / f"{stem}_caption_p1.png")

            figures.crop_figure_by_caption = fake_crop
            with tempfile.TemporaryDirectory() as tmp:
                scored = [type("Scored", (), {"paper": paper})()]
                out = figures.materialize_key_figures(scored, lambda p: f"{p.source}:{p.source_id}", output_dir=tmp)

            self.assertTrue(out)
            self.assertEqual(calls, ["recover"])
            self.assertTrue(paper.fulltext)
        finally:
            figures.fetch_pdf_bytes = old_fetch_pdf
            figures.extract_text_from_pdf = old_extract_text
            figures.crop_figure_by_caption = old_crop

    def test_readable_figure_check_does_not_apply_aesthetic_quality_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            blank = Path(tmp) / "blank.png"
            logo_like = Path(tmp) / "logo_like.png"
            sparse_strip = Path(tmp) / "sparse_strip.png"
            narrow_crop = Path(tmp) / "narrow_crop.png"
            valid = Path(tmp) / "valid.png"

            Image.new("RGB", (1200, 700), (0, 0, 0)).save(blank)

            logo = Image.new("RGB", (1100, 1700), (215, 236, 235))
            draw = ImageDraw.Draw(logo)
            draw.rounded_rectangle((16, 16, 1084, 1684), radius=90, outline=(0, 0, 0), width=16)
            logo.save(logo_like)

            strip = Image.new("RGB", (910, 201), "white")
            draw = ImageDraw.Draw(strip)
            draw.rounded_rectangle((40, 40, 870, 160), radius=24, outline=(0, 0, 0), width=10)
            strip.save(sparse_strip)

            crop = Image.new("RGB", (288, 718), "white")
            draw = ImageDraw.Draw(crop)
            draw.ellipse((48, 44, 240, 236), fill=(230, 220, 248), outline=(80, 70, 150), width=10)
            draw.line((144, 236, 144, 650), fill=(40, 40, 90), width=8)
            crop.save(narrow_crop)

            figure = Image.new("RGB", (1000, 700), "white")
            draw = ImageDraw.Draw(figure)
            for x in range(80, 930, 120):
                draw.line((x, 80, x, 620), fill=(215, 215, 215), width=2)
            for y in range(120, 620, 90):
                draw.line((70, y, 930, y), fill=(215, 215, 215), width=2)
            draw.rectangle((100, 120, 360, 360), fill=(226, 238, 255), outline=(40, 90, 180), width=8)
            draw.ellipse((430, 130, 760, 430), fill=(255, 231, 226), outline=(180, 60, 90), width=8)
            draw.rectangle((115, 455, 360, 610), fill=(232, 245, 232), outline=(50, 120, 70), width=6)
            draw.rectangle((660, 470, 900, 610), fill=(245, 235, 255), outline=(105, 70, 150), width=6)
            draw.line([(110, 600), (250, 470), (390, 520), (540, 310), (720, 380), (900, 180)], fill=(30, 120, 90), width=10)
            for index, x in enumerate(range(150, 860, 90)):
                y = 210 + (index % 4) * 70
                draw.ellipse((x, y, x + 34, y + 34), fill=(35, 95, 190))
                draw.line((x + 17, y + 17, min(930, x + 86), 540 - (index % 3) * 60), fill=(150, 80, 40), width=4)
            draw.text((105, 385), "robot navigation policy", fill=(40, 40, 40))
            draw.text((430, 455), "visual homing vector", fill=(40, 40, 40))
            draw.text((675, 620), "A  B  C  D", fill=(40, 40, 40))
            figure.save(valid)

            self.assertTrue(is_readable_image(blank))
            self.assertTrue(is_readable_image(logo_like))
            self.assertTrue(is_readable_image(sparse_strip))
            self.assertTrue(is_readable_image(narrow_crop))
            self.assertTrue(is_readable_image(valid))

    def test_existing_key_figure_keeps_any_readable_cached_figure(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            bad = output / "paper_p1_i0.png"
            good = output / "paper_p2_i0.png"
            Image.new("RGB", (1200, 700), (0, 0, 0)).save(bad)
            figure = Image.new("RGB", (1000, 700), "white")
            draw = ImageDraw.Draw(figure)
            for x in range(80, 930, 80):
                draw.line((x, 80, x, 620), fill=(200, 200, 200), width=2)
            draw.line([(100, 600), (220, 300), (360, 500), (520, 180), (800, 460)], fill=(30, 80, 180), width=12)
            draw.rectangle((620, 130, 900, 390), fill=(255, 231, 226), outline=(180, 70, 40), width=10)
            draw.rectangle((120, 120, 420, 330), fill=(226, 238, 255), outline=(40, 90, 180), width=8)
            for index, x in enumerate(range(150, 880, 85)):
                y = 170 + (index % 5) * 55
                draw.ellipse((x, y, x + 30, y + 30), fill=(30, 120, 90))
                draw.text((x, min(650, y + 34)), f"{index}", fill=(40, 40, 40))
            figure.save(good)

            cached = figures._existing_key_figure(output, "paper")

            self.assertEqual(cached, bad)
            self.assertTrue(bad.exists())

    def test_nature_formal_paper_prefers_publisher_asset_before_pdf(self):
        formal = Paper(
            source="openalex",
            source_id="W123",
            title="Nature paper with predictable media asset",
            abstract="",
            authors=[],
            venue="Nature Communications",
            published_at=date(2026, 5, 20),
            doi="10.1038/s41467-026-12345-6",
            pdf_url="https://example.com/slow.pdf",
            fulltext="Fig. 1. Overview.",
        )

        old_fetch_pdf = figures.fetch_pdf_bytes
        old_publisher = figures.materialize_publisher_key_figure
        try:
            calls = []

            def fake_fetch_pdf(_paper):
                calls.append("pdf")
                return b"%PDF fake bytes"

            def fake_publisher(_paper, output_dir, stem, figure_number=1):
                calls.append("publisher")
                path = output_dir / f"{stem}_publisher_fig{figure_number}.png"
                return write_valid_figure(path)

            figures.fetch_pdf_bytes = fake_fetch_pdf
            figures.materialize_publisher_key_figure = fake_publisher

            with tempfile.TemporaryDirectory() as tmp:
                scored = [type("Scored", (), {"paper": formal})()]
                out = figures.materialize_key_figures(scored, lambda paper: f"{paper.source}:{paper.source_id}", output_dir=tmp)

            self.assertTrue(out)
            self.assertEqual(calls, ["publisher"])
        finally:
            figures.fetch_pdf_bytes = old_fetch_pdf
            figures.materialize_publisher_key_figure = old_publisher

    def test_arxiv_paper_without_verified_figure_one_caption_is_rejected(self):
        paper = Paper(
            source="arxiv",
            source_id="2606.00001v1",
            title="Arxiv paper without hydrated fulltext",
            abstract="",
            authors=[],
            venue="arXiv",
            published_at=date(2026, 6, 1),
            pdf_url="https://arxiv.org/pdf/2606.00001v1",
        )

        old_fetch_pdf = figures.fetch_pdf_bytes
        old_extract_text = figures.extract_text_from_pdf
        try:
            figures.fetch_pdf_bytes = lambda _paper: b"%PDF synthetic"
            # A body reference is deliberately not a Figure 1 caption.
            figures.extract_text_from_pdf = lambda _pdf: "The method is shown in Fig. 1 and evaluated below."

            with tempfile.TemporaryDirectory() as tmp:
                scored = [type("Scored", (), {"paper": paper})()]
                out = figures.materialize_key_figures(scored, lambda p: f"{p.source}:{p.source_id}", output_dir=tmp)

            self.assertFalse(out)
            self.assertFalse(paper.key_figure_path)
        finally:
            figures.fetch_pdf_bytes = old_fetch_pdf
            figures.extract_text_from_pdf = old_extract_text

    def test_arxiv_companion_figure_keeps_formal_paper_visual_complete(self):
        formal = Paper(
            source="crossref",
            source_id="10.1126/scirobotics.aec1725",
            title="Extreme dynamic symmetry enables omnidirectional and multifunctional robots",
            abstract="Argus is a spherical robot.",
            authors=[],
            venue="Science Robotics",
            published_at=date(2026, 5, 20),
        )
        companion = Paper(
            source="arxiv",
            source_id="2605.29254v1",
            title="Extreme dynamic symmetry enables omnidirectional and multifunctional robots",
            abstract="",
            authors=[],
            venue="arXiv",
            published_at=date(2026, 5, 28),
            pdf_url="https://arxiv.org/pdf/2605.29254v1",
        )

        old_fetch_recent = figures.arxiv_fetch_recent_for_test if hasattr(figures, "arxiv_fetch_recent_for_test") else None
        old_fetch_pdf = figures.fetch_pdf_bytes
        old_extract_text = figures.extract_text_from_pdf
        old_crop = figures.crop_figure_by_caption
        old_queries = figures._arxiv_companion_queries
        try:
            figures._arxiv_companion_queries = lambda title: ['"exact title"']

            class FakeArxiv:
                @staticmethod
                def fetch_recent(query, max_results=5):
                    return [companion]

            import paper_radar.sources.arxiv as arxiv_module

            old_arxiv_fetch = arxiv_module.fetch_recent
            arxiv_module.fetch_recent = FakeArxiv.fetch_recent
            figures.fetch_pdf_bytes = lambda paper: b"%PDF fake bytes"
            figures.extract_text_from_pdf = lambda pdf, max_pages=6: "Fig. 1. Argus morphology overview."

            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)

                def fake_crop(_pdf, caption, output_dir, stem):
                    return write_valid_figure(output_dir / f"{stem}_caption_p1.png")

                figures.crop_figure_by_caption = fake_crop
                image = figures.materialize_arxiv_companion_key_figure(formal, out, "crossref_argus")

            self.assertIsNotNone(image)
            self.assertIn("Fig. 1", formal.key_figure_caption)
        finally:
            if old_fetch_recent is not None:
                figures.arxiv_fetch_recent_for_test = old_fetch_recent
            figures.fetch_pdf_bytes = old_fetch_pdf
            figures.extract_text_from_pdf = old_extract_text
            figures.crop_figure_by_caption = old_crop
            figures._arxiv_companion_queries = old_queries
            arxiv_module.fetch_recent = old_arxiv_fetch

    def test_arxiv_companion_is_limited_to_science_robotics(self):
        formal = Paper(
            source="crossref",
            source_id="10.1038/example",
            title="Formal paper with blocked figure",
            abstract="",
            authors=[],
            venue="Nature Communications",
            published_at=date(2026, 5, 20),
        )

        with tempfile.TemporaryDirectory() as tmp:
            image = figures.materialize_arxiv_companion_key_figure(formal, Path(tmp), "formal")

        self.assertIsNone(image)


if __name__ == "__main__":
    unittest.main()
