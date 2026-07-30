# Paper Radar

Paper Radar is a Codex skill and deterministic Python pipeline for preparing,
recovering, validating, rendering, and publishing a weekly embodied-perception
research digest.

## Install in Codex

This repository is public. No repository access grant or shared ChatGPT
workspace is required.

```bash
codex plugin marketplace add Guorong-He/paper-radar --ref main
```

Then open `/plugins`, select **Paper Radar Marketplace**, and install
**Paper Radar**. Start a new task and ask Codex to use Paper Radar. On first
use, the skill creates a private working copy of the bundled pipeline in the
current workspace; it never copies credentials or historical run data.

Publishing is optional and requires the installer to configure their own
repository, public URL, contact email, and GitHub token from `.env.example`.

## Public digest

- Latest issue: https://guorong-he.github.io/paper-radar/latest/
- Archived issues live under `/issues/YYYY-MM-DD/`.

## Shared package

The installable package includes the pipeline source, tests, research profile,
workflow documentation, and an empty state scaffold. It intentionally excludes
`.env`, API keys, databases, downloaded PDFs, generated output, and historical
issue state.
