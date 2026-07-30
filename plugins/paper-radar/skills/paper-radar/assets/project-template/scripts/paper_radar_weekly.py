#!/usr/bin/env python3
"""Run any Paper Radar CLI command against one deterministic weekly issue.

On Saturday and Sunday, ``upcoming-sunday`` resolves to the same issue date.
The wrapper injects that date before CLI parsing so every subprocess, queue,
cache, and stage ledger uses the same namespace without relying on prompt
memory or shell environment persistence.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_radar.cli import main  # noqa: E402


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if not arguments or arguments[0] != "--issue-date":
        sys.argv[1:1] = ["--issue-date", "upcoming-sunday"]
    main()
