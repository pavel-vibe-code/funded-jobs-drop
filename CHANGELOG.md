# Changelog

All notable changes to Funded Drop. Format follows [Keep a Changelog](https://keepachangelog.com/), versioning is [SemVer](https://semver.org/).

## v0.1.5 — 2026-05-15

Hotfix for a bug v0.1.4 introduced: Tracker rows for Favorites still landed with empty Title/Location even after the JD-fetch metadata-merge work shipped.

### Fixed
- **write_stage was reading from the wrong file.** v0.1.4 merged JD-derived title/location into the candidate dict inside `jd_fetch_stage`, which wrote the enriched candidate to `scorer-input-<idx>.json`. But `write_stage` continued reading candidate fields from `screener-survivors.json` (the pre-merge snapshot from discovery), so the merged metadata was discarded right before Notion write. Fix: write_stage now uses each `scorer-input-<idx>.json`'s candidate as the source of truth. Same-named row layout, but Title + Location now populate correctly for Favorites. Verified locally with smoke; next routine fire should write 13 Favorites rows with real titles instead of blank ones.

## v0.1.4 — 2026-05-15

Two bugs from production fire #2 (Favorites Pass A + missing structured fields) plus AI-50 seed reconciliation against drift.

### Fixed
- **Favorites bypass Pass A.** Favorites discovery is two-phase by design (returns IDs at discovery; title/location fill at JD fetch). With empty title + location, the screener marked everything `maybe` and dumped 473 unscoreable candidates onto Pass B. Now Favorites auto-promote from discovery directly to JD fetch + Pass B, skipping Pass A entirely. Saves screener tokens; eliminates the "all 416 land in Skim" failure mode. (`orchestrator.discovery_stage` writes `auto-promote-favorites.json`; `screener_aggregate` merges it into survivors with `_pass_a_verdict: auto`.)
- **Title + Location stay populated in Tracker.** `evaluation/jd_fetch.py:fetch()` and `fetch_jd_for_url()` now return `(jd_text, metadata, error)` 3-tuples. Each per-ATS fetcher (Greenhouse/Ashby/Lever) parses the rich response (title, location, work_mode, sometimes salary) alongside the description. The page-scrape fallback extracts the `<title>` tag. `jd_fetch_stage` merges metadata into the candidate dict before writing scorer-input, so write_stage's `cand.get("title")` returns the real ATS title instead of the discovery-time placeholder.

### Changed
- Discovery metrics now include `auto_promoted_favorites`. Screener stats include `auto_promoted_favorites` and the printout differentiates "screened" vs "auto-promoted."
- **AI-50 seed reconciliation.** `ai50_seed_loader.enable()` is now idempotent: re-running converges on the current `AI50_SEED` list. Identity is by NAME, not slug — so when a company moves ATSes or changes its slug, the existing Notion row is updated in place via `state.favorites.update_ats_config()` rather than creating a duplicate. Removed seed entries (Surge AI) get deactivated. Six v0.1.4 AI-50 corrections from live probing: Cohere `greenhouse:cohere → ashby:cohere` (129 jobs), Runway `greenhouse:runway → ashby:runway` (4 jobs), HeyGen `ashby:heygen → greenhouse:heygen` (25 jobs), World Labs `ashby:worldlabs → greenhouse:worldlabs` (12 jobs), Clay `ashby:clay → ashby:claylabs` (76 jobs), SambaNova kept from v0.1.3 (`greenhouse:sambanovasystems`, 24 jobs). Surge AI deactivated (no public ATS).
- **Greenhouse EU host removed** from `ats_adapters.py:GREENHOUSE_API_HOSTS`. The v1.5-inherited `boards-api.eu.greenhouse.io` is NXDOMAIN — never was a real endpoint. Greenhouse's EU data-residency boards are still served by `boards-api.greenhouse.io`. Removing it saves a per-board lookup against a known-bad host.

## v0.1.3 — 2026-05-15

First production routine fire surfaced two bugs that local e2e didn't catch.

### Fixed
- **Getro 403 in Cloud Routine egress.** All 5 Getro VCs (Accel, GC, Atomico, Index, Insight) returned 403 from the routine container. Root cause: `discovery/sources/getro.py` calls `api.getro.com` (the centralized Algolia-style API), but `INSTALL.md`'s allowed-domains list only included the per-VC subdomains (which are sent as `Origin` headers, not request targets). Adding `api.getro.com` to the routine egress allowlist fixes it. INSTALL.md updated; users with active routines need to add the host and re-fire.
- **Silent source-fetch failures.** The first production fire reported `errors_count: 0` and an empty `errors_summary` despite all 5 Getro sources failing. Root cause: `consider.fetch()`, `getro.fetch()`, `favorites.fetch()` swallowed per-VC errors via `print()` only. Refactored all three to return `(jobs, errors)` tuples; `discovery/runner.py` aggregates into `source_errors`; `orchestrator.py:_build_errors_summary()` composes them into the Runs DB row.
- **SambaNova AI-50 seed slug.** Was `(lever, "sambanova")` — that's a 404. SambaNova publishes on Greenhouse under `sambanovasystems` (24 active jobs verified). Fixed in `config/ai50_seed.py`.

### Added
- **AI-50 seed validation at enable time.** `ai50_seed_loader.enable()` now probes each ATS slug before writing to Favorites. Invalid slugs are skipped and logged with `invalid_slugs` in the return dict, so a stale seed entry doesn't accumulate broken Favorite rows. Affects HeyGen, Surge AI, World Labs, Clay (slugs need research for v0.1.4).
- **Broader allowed-domains in INSTALL.md** — adds the rest of the ATS registry's hosts (TeamTailor, Homerun, Comeet, SmartRecruiters, Workable, Recruitee, Personio, BambooHR) so non-AI-50 user Favorites work in Cloud Routine without per-add allowlist tweaks.

## v0.1.2 — 2026-05-15

User-facing docs + Cloud Routine quality-of-life.

### Added
- `INSTALL.md` — full setup guide: Notion → `/fd-setup` → Cloud Routine → maintenance.
- `CHANGELOG.md` (this file) seeded with v0.1.0 → v0.1.2 history.
- `README.md` substantial rewrite.
- `state/config.py`: env-var fallback for Cloud Routine. `FD_NOTION_TOKEN` + `FD_PARENT_PAGE_ID` are sufficient; the four DB IDs resolve at fire time via `client.list_child_databases()` matching canonical titles. Optional `FD_*_DB_ID` env vars skip discovery for one fewer API call per fire.
- `setup/runner.py`: `_guard_against_existing_workspace()`. Plain `/fd-setup` now refuses on a configured workspace and routes the user to `--repair` / `/fd-settings` / `--rewipe`. `--rewipe` verifies the existing parent page is archived in Notion (or 404) before re-creating DBs.

### Fixed
- `.claude/skills/`: skill files migrated from flat `<name>.md` to `<name>/SKILL.md` directory layout. Cloud Routine auto-discovery only matched the directory pattern; flat files worked locally via fuzzy fallback but failed in containers. All 7 skills migrated with `git mv` (history preserved).

## v0.1.1 — 2026-05-14

Post-test recovery work. The first live-fire e2e against a real Notion workspace revealed adapter gaps + a Notion-API regression we'd never have caught in dry-run. All fixes shipped while the test was running.

### Added
- `/fd-rescore` skill + orchestrator stages (`rescore_select`, `rescore_apply`). Three modes:
  - `failed` — retry `Status: jd_fetch_failed` rows after adapter improvements
  - `stale` — rows where `profile_hash_at_eval != current profile.profile_hash` (auto-fires post-`/fd-settings`)
  - `flagged` — rows where `Match quality != OK` (closes the qa learning loop)
- `state/tracker.py`: `read_rows_for_rescore(mode, current_profile_hash)`, `update_evaluated(page_id, verdict)`, `_auto_feedback_note()` helper.
- **Auto-exclude flag**: when scorer detects `pursue_blockers`, the Tracker row writes `Match quality = Feedback` + `[Auto]`-prefixed Feedback text. User can override by flipping back to OK — that override is a strong loosen-signal the qa agent reads next cycle.
- Generic page-scrape JD fallback (`evaluation/jd_fetch.py:_fetch_via_page_scrape`). Strips HTML to text via deterministic regex; threshold `MIN_SCRAPED_JD_CHARS=400` rejects SPA shells.
- `gh_jid` Greenhouse-API recovery for custom-domain SPAs (`_try_greenhouse_via_gh_jid`). Slug-from-host heuristic; falls back from scrape when the page is a hydration shell.

### Fixed
- **Schema-create bug** (`state/notion_client.py:create_database`). Notion API 2025-09-03 silently ignores `properties` at the top level of `POST /v1/databases` — schema must be passed via `initial_data_source.properties`. Fix also covers `setup/notion_init.py:validate_or_patch` which now patches at the data_source level and renames the auto-created `Name` title to whatever the schema wants (`Title` on Tracker, `Company` on Favorites).
- **Ashby JD fetch** (`evaluation/jd_fetch.py:_fetch_ashby_jd`). The per-job endpoint returns HTTP 401 on many boards. Switched to the list endpoint (`GET /posting-api/job-board/{slug}`) + per-board in-memory cache so multiple jobs from the same board fetch once. Live result: 0/2 → 2/2 recovery for the e2e test's Ashby URLs.
- JD-fetch coverage on the test data: **69% → 95%**.

### Released as v0.1.1
- VERSION 0.1.0 → 0.1.1.
- Tagged push to `pavel-vibe-code/funded-jobs-drop` main.

## v0.1.0 — 2026-05-14

First feature-complete release. Five build phases shipped end-to-end. Locally validated against Pavel's real Notion workspace + real Consider + Getro APIs.

### Added — Phase 5: polish + release

- `/fd-info` skill — command reference card (the user types `/fd-info` to see all available slash commands).
- `/fd-test-webhook` skill — POSTs a single test message to the configured webhook URL. Reports the HTTP result.
- `/fd-settings` skill — conversational editor for the Profile row (search prefs, salary floor, qa-learned rules, webhook config). Manual-only; not routine-compatible.
- `tests/smoke.py` — 41 dry-run checks: imports, full orchestrator chain on empty inputs, synthetic Pursue fixture, recycle_feedback prepare/apply, missing-output resilience, webhook formatting.

### Added — Phase 4: qa loop + closure detection + missed-fire

- `/fd-recycle-feedback` skill — orchestrates the learning loop (read feedback rows → qa agent synthesizes → write Profile.learned_*).
- `qa` agent (Sonnet) — reads feedback rows + current learned rules, returns refined `learned_exclusions` + `learned_examples`.
- `recycle_feedback.py` — Python CLI (`prepare` / `apply`).
- **Closure detection** in `orchestrator.discovery_stage` with per-source threshold protection. A Tracker row is only marked Closed if its `vc_source` returned ≥ `MIN_PER_SOURCE_FOR_CLOSURE=5` jobs this fire. Protects against single-source API failures cascading to false closures.
- **Missed-fire window widening**: `discovery/runner.py:effective_window_days()`. If the gap since last fire exceeds the profile window, widen to gap + 2 days, capped at 30. Recovery flag propagates to the summary.

### Added — Phase 3: real LLM scoring + orchestrator

- `screener` agent (Haiku) — Pass A triage on structured tags only (no JD fetched yet). Cheap pre-filter that kills 70-90% of candidates before the expensive Pass B.
- `scorer` agent (Opus) — Pass B per-job evaluation with full JD. Detects user-defined `pursue_blockers` + `stretch_indicators`. Outputs tier + reasoning + residency check + salary inference.
- `summarize` agent (Sonnet) — per-fire summary text for the Runs DB row.
- `orchestrator.py` — 5-stage CLI (`discovery`, `aggregate`, `jd_fetch`, `write`, `finalize`). Inter-stage state in `/tmp/fd-run/<run_id>/` (per-fire ephemeral; Cloud Routine compatible).
- `/fd-run` skill — Pattern B orchestrator: walks Claude through Python stages interleaved with parallel agent dispatches. Literal `<RUN_ID>` substitution (shell state doesn't persist between Bash tool calls). Bounded retry (1 per failed agent dispatch). WAVE_SIZE=8 cap on parallel dispatches.
- `notify/webhook.py` — tool-agnostic POST. Sends both `text` (Slack) and `content` (Discord) fields; works with Zapier/n8n/Teams. Errors non-fatal.
- `evaluation/jd_fetch.py` — source-aware JD fetcher. Getro: per-job detail page (parses `__NEXT_DATA__`). Consider + Favorites: native ATS via per-ATS endpoints.

### Added — Phase 2: end-to-end pipeline (stub Evaluation)

- `discovery/sources/consider.py` — 7-VC Consider fetcher (CSRF bootstrap + `POST /api-boards/search-jobs` paginated).
- `discovery/sources/getro.py` — 5-VC Getro fetcher (`POST /api/v2/collections/{network_id}/search/jobs`, no auth, Origin header).
- `discovery/sources/favorites.py` — ATS adapter dispatch via the active-IDs registry.
- `discovery/dedup.py` — first-wins canonical-URL dedup.
- `discovery/prefilter.py` — S2–S9 stages (work mode, country/relocation, seniority, company blacklist, industry exclusion, salary floor with FX-to-USD table).
- `discovery/runner.py` — orchestrates fetch → dedup → tracker-check → prefilter; returns survivors + metrics.

### Added — Phase 1: Notion workspace creation

- `state/notion_client.py` — direct Notion API client. Pinned `NOTION_API_VERSION = "2025-09-03"`. Retry/backoff with jitter, rate limiting (`MIN_INTERVAL_S = 0.34` for ~3 req/s margin). `validate_single_data_source()` guard against stray UI clicks creating a second data source on a DB.
- `state/{profile, tracker, favorites, runs}.py` — typed I/O for each DB. Schema-introspection-based: missing column → loud `SetupError` with repair guidance.
- `setup/{wizard, notion_init, runner, ai50_seed_loader}.py` — setup pipeline: `/fd-setup` walkthrough → `execute_fresh` creates 4 DBs + Profile row → optional AI-50 seed (14 supplement companies).

### Phase 0 — Repo bootstrap

- Initial structure, `.gitignore`, design principles in `CLAUDE.md`, the 12-VC roster, ATS adapter registry ported verbatim from parent.
