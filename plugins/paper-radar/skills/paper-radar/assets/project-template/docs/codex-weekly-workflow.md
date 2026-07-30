# Codex-native Paper Radar workflow

## Goal

Run Paper Radar weekly for high-quality embodied perception / robot perception research, publish a permanent archived issue plus a moving latest pointer, verify the public site, then email the permanent issue link to the current Gmail account.

The workflow has three active automations in Asia/Shanghai:

- `paper-radar-3-day-digest`: Saturday 03:00 preparation through
  `scripts/paper_radar_weekly.py`, which resolves to the following Sunday.
- `paper-radar-saturday-preflight`: Saturday 08:00 read-only verification of
  the 03:00 run, the existing authorized MyLOFT/one eligible publisher route,
  and Mac/network readiness. It reports user actions but never mutates issue
  state, downloads in batches, publishes, or emails.
- `paper-radar-sunday-publication`: Sunday 03:00 resume, publication,
  verification, email, and cleanup.

The preparation and publication jobs use `scripts/paper_radar_weekly.py`; on
Saturday and Sunday it resolves to the same upcoming-Sunday issue state, so
publication resumes preparation instead of repeating discovery or relying on a
remembered environment variable. The 08:00 preflight reads the same state.

The preflight is a local automation. If the Mac or Codex is not running at
08:00, the preflight itself cannot execute; its power-log check can only assess
the earlier 03:00 window once the job starts.

The automation must explicitly invoke `$paper-radar`. The repository skill is
the orchestration entry point; repository code and per-issue files remain the
source of truth for behavior and state.

## Inputs and archive state

- Workspace stays in place. Do not move `output/`, `site/`, `data/`, config, source, tests, scripts, or `.env`.
- `site/issues/` is required for historical archive indexing and non-repetition.
- Local `output/research_packet.json` may retain `fulltext` for analysis.
- Public `site/latest/research_packet.json` and `site/issues/YYYY-MM-DD/research_packet.json` must not expose `fulltext`.
- Resumable state lives under `data/issues/YYYY-MM-DD/`: `state.json`,
  `candidates.json.gz`, `run-events.jsonl`, `recovery-events.jsonl`, and
  `myloft-queue.json`. Never reset this directory to retry a downstream stage.

## Weekly run order

1. Read automation memory.
   - Use `/Users/guorong/.codex/automations/paper-radar-3-day-digest/memory.md`.
   - Carry forward standing requirements, especially the strict figure and duplicate gates.
   - Run `python scripts/paper_radar_weekly.py run-report` with the bundled runtime and
     resume from the recorded stage; do not assume every retry starts at discovery.

2. Run tests first.
   ```bash
   /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/run_tests_isolated.py
   ```
   Stop if tests fail. This wrapper redirects working-set, cache, queue,
   recovery-audit, ledger, and source-status paths to a temporary directory and
   verifies that production state did not change.

3. Prepare the weekly packet.
   ```bash
   PYTHON=/Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
   PYTHONPATH=src $PYTHON -m paper_radar.cli --issue-date previous-sunday warm-candidate-cache
   $PYTHON scripts/paper_radar_weekly.py prepare-weekly
   ```
   Normal runs keep all configured sources enabled unless a source is actually down. Do not disable OpenAlex for routine runs.

   Same-issue retries reuse `candidates.json.gz` and must not refetch the
   three-year window. A new issue starts from the latest prior candidate
   catalog, fetches only the configured recent incremental window, merges and
   deduplicates it, then trims to the allowed lookback. Use
   `--refresh-sources` only for a corrupt/stale cache, a material discovery
   configuration change, or an explicitly requested full refresh. An empty
   all-source result is not saved as a usable cache, so the next retry probes
   sources again.

   The warm command is read-only with respect to the database and creates only
   a compact metadata catalog. It does not overwrite a non-empty catalog unless
   `--force` is explicit. Crossref/OpenAlex queries, database warming, cache
   reads, and cache merges all enforce the same publication bounds: configured
   lookback through issue date plus seven days. Far-future metadata is rejected.

   Before normal ranking, scan the configured local Downloads folder for recently added academic PDFs, whether downloaded by Codex or manually by the user. Match them to the already in-scope current candidate pool by DOI/title, validate PDF identity and research-paper status, import them into the local recovery cache, and prioritize them inside their original Tier. A valid late-arriving PDF must revive a candidate that was prematurely marked skipped and clear its terminal recovery flags. Do not require the user to rename, move, or manually import the file. Download priority does not bypass the explicit topic exclusions, primary-research gate, Tier/formal quotas, or archive-wide duplicate check.

   During preparation, Paper Radar first forms the strict Tier 1/Tier 2/preprint provisional mix from metadata. It then uses its normal publisher/PDF path for those formal candidates before applying the Figure 1 gate. A selected Tier 1/Tier 2 paper whose official PDF is missing, 403/404, HTML/preview content, or cannot yield a valid key figure follows this fixed recovery order: (1) official publisher path; (2) one direct official-PDF attempt through the logged-in, authorized MyLOFT Chrome session; (3) only if that current MyLOFT attempt is recorded unavailable, automatically search for the same work's arXiv version, author public manuscript, or institutional-repository PDF. The third layer may use only approved publisher-direct assets, official APIs, and open-access indexes; it must never use Sci-Hub, LibGen, Tor, CAPTCHA/Cloudflare bypass, stealth/anti-detection, `smart_download`, or `auto_setup`.

   A lawful public version remains a full-text source for its original formal paper: retain its original formal venue, Tier, score, and quota; do not reclassify it as a preprint. Accept it only when the source is approved and it passes PDF signature, size, page-count, parseable-text, and same-work title-identity checks. Recovered PDFs and full text remain local under `output/recovered_pdfs/`; provenance must distinguish MyLOFT, arXiv, author-public-manuscript, and institutional-repository sources; current-run outcomes are appended to `data/issues/YYYY-MM-DD/recovery-events.jsonl`; public packets must still omit `fulltext`. If an unattended run needs a fresh login or MFA, do not wait indefinitely or solicit a password: report that authorized session renewal is required and continue replacement/quality-gate logic.

   Downloads are reconciled at three points: before selection, immediately
   before recovery, and after recovery/figure extraction. Each scan is labeled
   in `output/local_download_intake.json`; a valid late arrival triggers only
   the incomplete selected-paper recovery work, not a full source restart.

4. Use the Tsinghua MyLOFT queue only when it is actually needed.
   - MyLOFT is not a discovery crawler. Only a Tier 1/2 formal paper that has already passed the strict metadata semantic audit may enter `data/issues/YYYY-MM-DD/myloft-queue.json` after its normal official publisher path fails. Keep all such failures in the metadata queue so later high-relevance papers are not hidden by earlier failures. A current MyLOFT failure releases that same formal paper to the automatic lawful-public third layer; it is not a reason to lower venue quality, change the Tier mix, or count the recovered version as a preprint.
   - At the start of every new preparation run, reconcile pending queue records against that run's strict semantic candidate set. Retire stale pending records with an audit reason; preserve terminal records and local files. Historical discovery or earlier-session notes are audit evidence, not permanent candidate exclusions: a current strict formal selection must reach one direct publisher-download attempt in the authorized MyLOFT Chrome session. After that direct attempt fails, retain its terminal result and use a same-tier replacement; do not repeatedly hammer it. A historical queue entry must never bypass the current platform-and-task evidence gate.
   - First inspect `output/prepare_status.json`. If the packet is already publishable, do not download anything from MyLOFT even when candidates remain in the queue.
   - Otherwise run `python scripts/paper_radar_weekly.py myloft-status`. Process at most one pending paper at a time in the visible, already authorized Chrome/MyLOFT session.
   - Sort the queue by research relevance first, then Tier 1 status, total score, and recency. Only the first 8 remaining-budget candidates are download-eligible at one time.
   - Never use a batch downloader or parallel tabs. Start no more than 8 MyLOFT downloads per issue, no more than 10 in any rolling 24 hours, and wait at least 60 seconds between download starts.
   - Import each finished PDF with `python scripts/paper_radar_weekly.py myloft-import --doi DOI --pdf /path/to/file.pdf`. This preserves the original download and validates PDF signature, size, pages, parseable text, title and DOI before recording local MyLOFT/Tsinghua provenance.
   - Rerun `prepare-weekly` after every successful import. Stop institutional downloading immediately once the 10-paper packet passes the quality gate.
   - If entitlement is unavailable or the item has no usable research PDF, run `python scripts/paper_radar_weekly.py myloft-skip --doi DOI --reason "..."`; do not repeatedly hammer the publisher.
   - A direct MyLOFT failure releases the automatic same-work public recovery; it does not itself remove the paper from selection. Only after that public recovery also fails may the pipeline retire the paper and select a same-tier replacement, so failed records are never retried indefinitely or used to block the issue. If three distinct papers from one exact formal venue have completed both failures in the current issue, treat only that venue as temporarily unavailable for this issue and select another same-tier formal venue; this is a recovery circuit breaker, not a relevance, Tier, or permanent venue rule.
   - Login, MFA, and CAPTCHA require the user in the visible browser. Never export passwords, cookies, localStorage, or browser tokens.

5. Enforce paper-selection gates.
   - Token budget, runtime, or convenience must never be used to relax selection, full-text, figure, duplicate, analysis, or visual-QA gates. If the gates are not met, keep recovering/replacing candidates or mark the issue not ready; do not publish a lower-quality issue.
   - Exactly 10 final papers.
   - Aim for 3 tier-1, 4-5 tier-2, and 2-3 arXiv/preprint papers.
   - Before any final-paper full-text reading, create and inspect the metadata-only `output/candidate_audit.json`. Every final paper must carry both explicit evidence of an embodied platform (for example robot, drone, e-skin/bioelectronic device, or a named embodied sensing system) and a concrete embodied task (perception/sensing, manipulation, locomotion, navigation, contact/force, teleoperation, or robot control). Mark it `direct_embodied`; do not use venue prestige, score, figures, generic AI/RL, `swarm`, `control`, or `bioinspired` wording as a substitute. For formal Nature-family candidates, also inspect only the landing-page article-type metadata before queueing any download: reject `News & Views`, commentary, editorial, review, and perspective records, and replace them with same-tier primary research papers.
   - Reject quantum information/computing, geology/geophysics, biology/nutrition/animal-behaviour papers, and generic ML papers unless the metadata explicitly establishes the robot or embodied system and task. A broadly relevant transferable item may be used only when narrowly justified and no more than two final papers may be transferable; the target is at least eight direct embodied papers.
   - Tier 1 and Tier 2 remain formal-venue quality buckets, not relevance overrides. Tier 1 targets 3 papers and Tier 2 targets 4–5; preprints target 2–3. Tier 1 retrieval must cover the configured three-year backfill window rather than only each journal's newest generic batch, because relevant embodied-robotics work is sparse. For the configured Tier 2 journals, also use their bounded robot/perception query recall rather than using *Device* as a quota fallback. If the semantic audit cannot yield exactly 10 with this mix, stop rather than drifting off-scope or preserving an older packet.
   - Never exceed 3 arXiv/preprints.
   - Do not include Scientific Reports.
   - Do not include papers from *Device*. Treat it as an excluded venue, not as a same-tier fallback, even when its topic or full-text route looks suitable.
   - Do not lower formal venue quality below the configured tier-1/tier-2 profile.
   - Recent papers are preferred first; fallback only within the configured recent-3-year window.

6. Enforce strict duplicate gate.
   - Run `python scripts/paper_radar_weekly.py history-check` and consume only its compact JSON report. A non-zero overlap exits with status 2.
   - Do not open, paste, summarize, or load historical `research_packet.json`, `analyses.json`, HTML, or full text into the model context for deduplication.
   - The command maintains `data/recommendation_history_index.json`, containing only issue dates and canonical DOI/source/title keys. It invalidates automatically when archived packet metadata changes.
   - Candidate exclusion still covers all prior archived issue papers under `site/issues/YYYY-MM-DD/`.
   - Same-day reruns may keep or replace the same issue papers.
   - Read both top-level list packets and `{ "papers": [...] }` packets.
   - Normalize DOI URL prefixes, including `doi:`, `doi.org`, and `dx.doi.org`.
   - Normalize titles by alphanumeric tokens, not only whitespace.
   - Canonicalize arXiv source IDs across version suffixes such as `v1` and `v2`.
   - If the compact report's `overlap_count` is nonzero, replace only the listed papers and rerun the command; otherwise proceed without further history inspection.

7. Enforce strict image gate.
   - `key_figure_path` presence alone is not enough. Every final figure must result from a successful `Fig 1`/`Fig. 1`/`Figure 1` (including 1A) caption extraction and be recorded in `output/figure_audit.json`. Do not require publisher-specific punctuation, but never crop from an inline body reference or select an arbitrary first large PDF image.
   - Do not apply aesthetic, density, contrast, aspect-ratio, or dimensions-based image-quality scoring. A complete, decodable screenshot of the verified Figure 1 is sufficient.
   - Generate and inspect a contact sheet to confirm that each final asset is the complete Figure 1 corresponding to its verified caption; do not reject a legitimate Figure 1 merely because it is sparse, narrow, or visually simple.
   - Generate and inspect a Figure 1 contact sheet for the final ten before publishing. If 10 valid, verified Figure 1 images cannot be reached without lowering quality, stop rather than publishing.

8. Write analyses.
   - Read `output/research_packet.json`.
   - Write `output/analyses.json` in concise Chinese academic-brief style.
   - Every paper must include:
     - `core_insight`
     - `problem_frame`
     - `first_principles`
     - `mechanism`
     - `boundary_advanced`
     - `old_problem`
     - `why_it_works`
     - `true_novelty`
     - `evidence_summary`
     - `email_summary`
     - `importance_reason`

9. Render Journal Edition.
   ```bash
   /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/paper_radar_weekly.py render-from-analyses
   ```
   Verify `output/digest.html` includes the Journal Edition layout, tag filters, score badges, original/PDF links, expandable deep-reading sections, and `id="archive-link"` / `历史推荐`.
   After content generation, perform a visual layout check before publishing. Use browser screenshots or an equivalent DOM bounding-box audit at desktop and mobile widths. Confirm there is no text overlap, clipping, or hidden overflow in the masthead lede, right-side editorial panel, paper cards, score badges, tag filters, action links, and archive navigation. If layout fails, shorten editorial copy or adjust presentation details before continuing.

10. Build the static site.
   ```bash
   /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/paper_radar_weekly.py build-site --public-url "${PAPER_RADAR_PUBLIC_URL:-https://guorong-he.github.io/paper-radar/}"
   ```
   This must create:
   - `site/issues/YYYY-MM-DD/index.html`
   - `site/latest/index.html`
   - `site/issues/index.html`

11. Verify local site outputs.
    - `latest/`, `issues/YYYY-MM-DD/`, and `issues/` all contain Journal UI markers.
    - Latest and permanent issue pages contain `id="archive-link"` and `历史推荐`.
    - Public packet files in `site/` contain 10 papers, 10 figures, and no `fulltext`.
    - Latest and permanent issue pages pass the no-overlap visual layout check on desktop and mobile.

12. Publish GitHub Pages.
    ```bash
    PYTHONPATH=src /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/publish_pages.py
    ```
    The publisher may use git push or GitHub Contents API fallback. Do not fake success if credentials are missing.

13. Verify public output before email.
    ```bash
    /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/paper_radar_weekly.py verify-publication --public-url "${PAPER_RADAR_PUBLIC_URL:-https://guorong-he.github.io/paper-radar/}"
    ```
    Confirm:
    - permanent issue HTML is refreshed;
    - latest HTML is refreshed;
    - archive HTML is refreshed;
    - issue/latest public JSON contain the same 10 papers;
    - all 10 public papers have key figures;
    - no public JSON exposes `fulltext`;
    - Journal Edition / Journal Archive markers are present;
    - `archive-link` / `历史推荐` is present on issue/latest pages.

    Successful `build-site` and `verify-publication` commands write their stage
    checkpoints automatically. If a separate publisher performs the push,
    record it with `scripts/paper_radar_weekly.py mark-stage --stage publication --status complete`.

14. Email delivery.
    - Get the current Gmail profile.
    - Send to the same Gmail account.
    - If public verification passed, send `output/email-link.html` as HTML and do not attach files.
    - If publishing or public verification failed, send fallback poster/zip attachments and clearly state that the online link is not configured or not refreshed.
    - Record the final result with `scripts/paper_radar_weekly.py mark-stage --stage email_delivery --status complete` (or `failed` with a concise detail).

15. Cleanup after successful public verification and email.
    ```bash
    /Users/guorong/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/paper_radar_weekly.py clean-workspace --older-than-days 30 --apply
    ```
    Cleanup may remove old transient `output/` artifacts only. It must not touch `site/`, `data/`, current packet/analyses/digest/email files, or `output/figures/`.

## Run summary requirements

The final run summary must report:

- paper count
- figure count
- formal-venue count
- tier-1 count
- tier-2 count
- arXiv/preprint count
- strict history-overlap count
- whether the stricter image-quality gate passed
- whether a contact-sheet or equivalent visual audit was performed when needed
- permanent issue URL
- latest URL
- history archive URL
- whether Journal Edition and Journal Archive were published and verified
- whether history navigation was verified
- whether the no-overlap visual layout check passed
- whether email was sent
- MyLOFT pending/imported/skipped counts and rolling-rate status
- the compact `run-report` stage status, including the exact partial or failed stage if incomplete

## Design principles

- A completed current-issue paper slot is frozen across preparation passes. A failed paper is replaced only by another paper in the same Tier; partial success must be reported accurately and must never be reset to zero.
- Candidate discovery, recovery, analysis/render, site build, public verification,
  publication, and email delivery are separate checkpoints. A retry resumes the
  first incomplete stage and preserves completed stages.
- Durable issue JSON writes are atomic and recovery/run ledgers are append-only;
  tests always run with redirected temporary state.
- Two consecutive approved public-recovery timeouts exhaust that paper for the current issue and trigger same-Tier replacement, preventing an unavailable paper from making the workflow spin indefinitely.
- Quality gates are invariant across manual and automated runs; token-saving is not an accepted reason to shorten analysis or skip verification.
- Do not use abstract paraphrase as a substitute for scientific understanding.
- If full text is missing, state the evidence limit in the analysis.
- Figures are a publication-quality hard gate, not a checkbox.
- Text layout is a publication-quality hard gate too: no overlapping, clipped, or hidden issue copy should be published.
- Duplicate exclusion is archive-wide and canonicalized.
- Codex handles scientific judgment and Chinese analysis; local scripts handle repeatable fetching, filtering, rendering, publishing, and verification.
