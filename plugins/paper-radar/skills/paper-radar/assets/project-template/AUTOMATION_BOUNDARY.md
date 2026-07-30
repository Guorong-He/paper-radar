# Paper Radar Automation Boundary

This directory is an active automation workspace for `paper-radar-3-day-digest`.

Do not move, rename, delete, deduplicate, or archive this folder or its runtime
subdirectories during general file organization tasks. In particular, preserve:

- `.env`
- `config/`
- `data/`
- `output/`
- `site/`
- `src/`
- `tests/`
- `scripts/`
- `.agents/skills/paper-radar/`

The `site/issues/` tree is the local archive source used to avoid repeating
papers in later Paper Radar issues.

The `data/issues/YYYY-MM-DD/` tree is the resumable source of truth for each
issue. Never clear it as a generic retry or test setup step.
