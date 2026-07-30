#!/usr/bin/env python3
"""Create a writable Paper Radar workspace from the bundled clean template."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = SKILL_ROOT / "assets" / "project-template"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="New or empty directory that will receive the Paper Radar project.",
    )
    args = parser.parse_args()

    destination = args.destination.expanduser().resolve()
    if not TEMPLATE_ROOT.is_dir():
        raise SystemExit(f"Bundled project template is missing: {TEMPLATE_ROOT}")

    if destination.exists() and any(destination.iterdir()):
        raise SystemExit(
            f"Refusing to overwrite non-empty destination: {destination}"
        )

    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_ROOT, destination, dirs_exist_ok=True)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
