from pathlib import Path
from typing import BinaryIO

from PIL import Image


def is_readable_image(image_or_path: Image.Image | Path | str | BinaryIO) -> bool:
    """Accept any decodable image; Figure 1 identity is the quality gate.

    Paper Radar deliberately does not score composition, density, contrast,
    dimensions, or aspect ratio.  A complete screenshot of a verified Figure 1
    may legitimately be sparse, unusually shaped, or visually simple.  This
    check exists only to reject a missing, corrupt, or non-image response.
    """

    try:
        if isinstance(image_or_path, Image.Image):
            image = image_or_path.copy()
        else:
            image = Image.open(image_or_path)
        image = image.convert("RGB")
        image.load()
    except Exception:
        return False

    width, height = image.size
    return width > 0 and height > 0
