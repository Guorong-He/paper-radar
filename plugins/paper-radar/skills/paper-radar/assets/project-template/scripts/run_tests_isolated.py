#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = [
    ROOT / "data" / "paper_radar.db",
    ROOT / "data" / "myloft_rate_limit.json",
    ROOT / "data" / "recommendation_history_index.json",
    ROOT / "output" / "research_packet.json",
    ROOT / "output" / "prepare_status.json",
    ROOT / "output" / "candidate_audit.json",
    ROOT / "output" / "figure_audit.json",
    ROOT / "output" / "local_download_intake.json",
    ROOT / "output" / "analyses.json",
    ROOT / "output" / "digest.json",
    ROOT / "output" / "digest.md",
    ROOT / "output" / "digest.html",
    ROOT / "output" / "email.html",
    ROOT / "output" / "issue_working_set.json",
    ROOT / "output" / "source_status.json",
    ROOT / "output" / "scansci_recovery_audit.jsonl",
    ROOT / "output" / "myloft_download_queue.json",
]


def main() -> int:
    before_paths = set(PROTECTED) | set(_production_state_files())
    before = {path: _snapshot(path) for path in before_paths}
    with tempfile.TemporaryDirectory(prefix="paper-radar-tests-") as tmp:
        sandbox = Path(tmp)
        env = os.environ.copy()
        env.update(
            {
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PAPER_RADAR_STATE_ROOT": str(sandbox / "state"),
                "PAPER_RADAR_WORKING_SET_PATH": str(sandbox / "state.json"),
                "PAPER_RADAR_CANDIDATE_CACHE_PATH": str(sandbox / "candidates.json.gz"),
                "PAPER_RADAR_RUN_LEDGER_PATH": str(sandbox / "run-events.jsonl"),
                "PAPER_RADAR_MYLOFT_QUEUE_PATH": str(sandbox / "myloft-queue.json"),
                "PAPER_RADAR_MYLOFT_LEDGER_PATH": str(sandbox / "myloft-ledger.json"),
                "PAPER_RADAR_SCANSCI_AUDIT_PATH": str(sandbox / "recovery-events.jsonl"),
                "PAPER_RADAR_SOURCE_STATUS_PATH": str(sandbox / "source-status.json"),
            }
        )
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", *sys.argv[1:]]
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False)

    after_paths = set(PROTECTED) | set(_production_state_files())
    all_paths = before_paths | after_paths
    changed = [path for path in all_paths if _snapshot(path) != before.get(path)]
    if changed:
        for path in changed:
            _restore(path, before[path])
        print(
            "Isolated test guard restored unexpected production-state writes: "
            + ", ".join(str(path.relative_to(ROOT)) for path in changed),
            file=sys.stderr,
        )
        return 2
    return completed.returncode


def _production_state_files() -> list[Path]:
    root = ROOT / "data" / "issues"
    return [path for path in root.glob("*/*") if path.is_file()] if root.is_dir() else []


def _snapshot(path: Path) -> tuple[str, bytes] | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(payload).hexdigest(), payload


def _restore(path: Path, snapshot: tuple[str, bytes] | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(snapshot[1])


if __name__ == "__main__":
    raise SystemExit(main())
