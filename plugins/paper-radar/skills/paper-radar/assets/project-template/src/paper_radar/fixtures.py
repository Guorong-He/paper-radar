from datetime import date, timedelta

from .models import Paper


def sample_papers(today: date):
    return [
        Paper(
            source="fixture",
            source_id="soft-001",
            title="Multimodal Tactile Perception for Soft Robots",
            abstract="We present a soft robot sensing system that achieves state-of-the-art benchmark performance and validates on a real robot platform.",
            authors=["Kevin Y. Chen"],
            venue="Nature Machine Intelligence",
            published_at=today - timedelta(days=2),
            url="https://example.com/soft-001",
        ),
        Paper(
            source="fixture",
            source_id="vision-001",
            title="Robust Visual State Estimation under Severe Deformation",
            abstract="A transferable perception method for state estimation that outperforms prior work on public benchmarks and generalizes in real-world settings.",
            authors=["A. Researcher"],
            venue="arXiv",
            published_at=today - timedelta(days=4),
            url="https://example.com/vision-001",
        ),
        Paper(
            source="fixture",
            source_id="wing-001",
            title="Event-Based Perception for Flapping-Wing Micro Air Vehicles",
            abstract="We study flapping wing vehicles and show real-world hardware experiments for lightweight onboard perception.",
            authors=["B. Researcher"],
            venue="SenSys",
            published_at=today - timedelta(days=8),
            url="https://example.com/wing-001",
        ),
        Paper(
            source="fixture",
            source_id="noise-001",
            title="Medical Image Segmentation with Large Language Model Survey",
            abstract="A broad review of medical image segmentation methods.",
            authors=["C. Researcher"],
            venue="arXiv",
            published_at=today - timedelta(days=1),
            url="https://example.com/noise-001",
        ),
    ]
