---
name: paper-radar
description: Run, resume, diagnose, and publish the weekly embodied-perception Paper Radar. Use when preparing a weekly issue, recovering PDFs or Figure 1 assets, inspecting a failed or partial run, resuming publication or email delivery, or changing the Paper Radar automation.
---

# Paper Radar

Operate the bundled deterministic Python pipeline as the source of truth. Use
this skill for orchestration and scientific judgment; keep persistent state in
the user's Paper Radar workspace, never in the installed skill or model memory.

## Locate or create the workspace

Treat a directory containing both `pyproject.toml` and `src/paper_radar/` as a
Paper Radar project root. Search the current directory and its ancestors first.
If none exists, use the absolute directory containing this `SKILL.md` as
`SKILL_DIR`. When its bundled bootstrapper exists, create a writable project:

```bash
python3 "$SKILL_DIR/scripts/bootstrap_project.py" \
  --destination "$PWD/paper-radar"
cd "$PWD/paper-radar"
```

The bootstrapper refuses to overwrite a non-empty destination. If this is a
repository-local skill without the bundled bootstrapper, ask the user to open
the cloned Paper Radar repository root. Never copy or publish another user's
`.env`, databases, downloaded PDFs, generated output, browser state, or issue
history.

Create the runtime once, then reuse it:

```bash
python3 -m venv .venv
PYTHON="$PWD/.venv/bin/python"
"$PYTHON" -m pip install -e .
export PYTHONPATH="$PWD/src"
```

If `.venv/bin/python` already exists, skip environment creation and
installation. Read `docs/codex-weekly-workflow.md` completely before a full
weekly run or an automation change. Also read the automation memory when the
scheduled task provides one.

Before publishing, require the user to configure their own `.env` from
`.env.example`. Never reuse the publisher's repository, token, contact email,
or authenticated browser session. Publication and email delivery are external
write actions and require the user's explicit request.

## Start or resume

Work from the project root:

```bash
"$PYTHON" scripts/paper_radar_weekly.py run-report
```

Resume from the report and existing artifacts. Do not delete or reset partial
issue state. The current issue's durable state is under
`data/issues/YYYY-MM-DD/`; completed paper slots are frozen and only an
incomplete slot may be replaced within the same Tier.

## Safe execution order

1. Run the isolated suite before changing or publishing anything:

   ```bash
   "$PYTHON" scripts/run_tests_isolated.py
   ```

   This command must not modify production issue state.

2. Prepare or resume the issue:

   ```bash
   "$PYTHON" scripts/paper_radar_weekly.py prepare-weekly
   "$PYTHON" scripts/paper_radar_weekly.py run-report
   ```

   The wrapper resolves both Saturday and Sunday to the same upcoming-Sunday
   issue date. Do not depend on a prompt or shell export to carry the date.
   Before the first preparation pass, ensure the previous issue has a bounded
   database baseline with:

   ```bash
   "$PYTHON" -m paper_radar.cli --issue-date previous-sunday warm-candidate-cache
   ```

   A same-issue retry reuses its candidate cache. Do not pass
   `--refresh-sources` merely because a downstream step failed. Use it only
   when the cache is corrupt or stale, the discovery configuration materially
   changed, or the user explicitly requests a full refresh. Candidate dates
   must remain inside the configured lookback and no more than seven days
   after the issue date.

3. Inspect `output/prepare_status.json`, `output/candidate_audit.json`, and
   `output/figure_audit.json`. Continue recovery or same-Tier replacement until
   `ready_to_publish` is true. Never publish a preserved older packet or relax
   the ten-paper, semantic, duplicate, full-text, Tier, or verified-Figure-1
   gates.

4. For a required authorized MyLOFT recovery, follow the sequential and
   rate-limited procedure in `docs/codex-weekly-workflow.md`. Rerun
   `prepare-weekly` after a completed import or recorded skip. The pipeline
   scans Downloads before selection, before recovery, and after recovery, so a
   late valid PDF can be reconciled without restarting discovery.

5. Run the compact duplicate gate, write the current analyses, and render:

   ```bash
   "$PYTHON" scripts/paper_radar_weekly.py history-check
   "$PYTHON" scripts/paper_radar_weekly.py render-from-analyses
   ```

6. Perform the required visual QA, build and publish the site, verify the
   public result, and only then deliver email. Record external stages when a
   connector or separate script performs them:

   ```bash
   "$PYTHON" scripts/paper_radar_weekly.py mark-stage --stage publication --status complete
   "$PYTHON" scripts/paper_radar_weekly.py mark-stage --stage email_delivery --status complete
   "$PYTHON" scripts/paper_radar_weekly.py run-report
   ```

## Failure rules

- Report the exact failed stage and preserve all successful checkpoints.
- Treat an empty all-source result as retryable; never persist it as a usable
  candidate cache.
- Prefer same-issue cached discovery and incremental refresh from the most
  recent candidate catalog. A full three-year refetch is the fallback, not the
  default retry behavior.
- Do not infer success from process exit alone. Publishing requires
  `ready_to_publish`, local gates, visual QA, and public verification.
- Never move or clean `data/`, `site/issues/`, `output/recovered_pdfs/`, or the
  current issue state during recovery.
