# Changelog

All notable changes to Funded Drop. Format follows [Keep a Changelog](https://keepachangelog.com/), versioning is [SemVer](https://semver.org/).

## v0.1.17 — 2026-05-16

Workday favorites are region-filtered *before* the per-job JD fetch. Workday mega-tenants (MSD, Nvidia, Adobe) carry ~500 jobs each, mostly outside an EU/US-scoped profile's region — previously every one got auto-promoted and JD-fetched, then dropped post-JD. Now the obviously-out-of-region ones are dropped on a location string already in hand.

### Added
- **Pre-JD region filter for Workday favorites.** Workday's CXS *list* response carries each posting's `locationsText`, so the Favorites source (`_workday_jobs`) classifies location against the profile's variant region and drops out-of-region jobs before they become candidates. `fetch_workday_postings()` returns posting dicts (location + title); `fetch_active_ids_workday()` is now a thin wrapper over it. Measured live: **~50% dropped pre-JD** (764/1500 across MSD+Nvidia+Adobe). The rest are in-region or multi-location reqs — Workday collapses those to "N Locations" in the list, genuinely unresolvable without the detail call, so they're kept and deferred to post-JD screening.
- **`location_in_variant_region()`** in `prefilter` — variant-region check on a bare location string (vs `apply`'s DiscoveredJob-based S1a). Returns True / False / None; None (ambiguous) is kept, same lax policy as S1a.
- **ISO-3166 alpha-3 country recognition.** Workday writes location as `DEU - Berlin - …`; `_country_from_text` resolves a leading `XXX - ` ISO-3 token — anchored to the start and separator-gated, so English-word codes (AND/ARE/CAN) can't false-match.
- **Location-data expansion** for enterprise-tenant coverage: `_country_from_text` now matches the full ISO-3 country set (+ "Korea"), all 50 US states (`Remote Illinois` → United States), and ~18 more global hub cities (San Jose — 103 Adobe reqs alone — Noida, Seoul, …). Also sharpens VC-job S1a/S3 detection.

### Note
Eliminating the residual multi-location JD-fetches would need server-side CXS faceting (querying Workday with an EU location facet) — a larger, tenant-specific change, deferred.

## v0.1.16 — 2026-05-16

Workday adapter validated end-to-end against the live CXS API (the wd5 pod returned from maintenance). One pagination bug found and fixed.

### Fixed
- **`fetch_active_ids_workday` stopped after 2 pages for large tenants.** Workday's CXS `total` field is accurate only on the *first* page — it returns `0` on every page after. The v0.1.15 loop's `offset >= total` termination check therefore fired on page 2 (`40 >= 0`), silently capping Nvidia and Adobe at 40 jobs each. (MSD escaped only by hitting the 25-page cap first.) Termination now keys off a short page — `len(postings) < WORKDAY_PAGE_SIZE` — and never consults `total`. Re-verified: MSD/Nvidia/Adobe each return the full 500-job cap; JD fetch resolves title/location/work_mode.

### Validated
- CXS list + detail contract confirmed live: `total`, `jobPostings[].externalPath`, `jobPostingInfo.{jobDescription,title,location,remoteType}` — all exactly as scaffolded in v0.1.15. No other drift.
- Large Workday favorites are capped at 500 jobs (`WORKDAY_MAX_PAGES` × `WORKDAY_PAGE_SIZE` = 25 × 20). MSD has ~823 open reqs, Nvidia ~2000, Adobe ~1178 — the cap trades completeness for a bounded ~25 list calls/favorite.

## v0.1.15 — 2026-05-16

Workday ATS adapter — unlocks the large enterprise tail (MSD, Adobe, Nvidia, Resideo and the long list of companies on Workday rather than a startup ATS). Favorites-only, like the other direct adapters.

### Added
- **Workday adapter (`workday`).** New entry in `ATS_ADAPTERS`. Workday is the odd one out: no single API host — each tenant lives on its own subdomain + datacenter pod (`msd.wd5.myworkdayjobs.com`). Since tenant/pod/site aren't derivable from a company name, a Workday Favorite carries its **full careers URL in `careers_url`** and `parse_workday_url()` decomposes it into `(host, tenant, site, external_path)`. `ats_slug` is optional for Workday (the Favorites guard was relaxed accordingly).
  - **Discovery:** `fetch_active_ids_workday()` drives the CXS list endpoint — `POST /wday/cxs/{tenant}/{site}/jobs`, offset-paginated 20/page, capped at `WORKDAY_MAX_PAGES` (25 → 500 jobs/favorite). Returns each posting's `externalPath`, which doubles as the job id.
  - **JD fetch:** `_fetch_workday_jd()` hits the CXS detail endpoint — `GET /wday/cxs/{tenant}/{site}{externalPath}` — and strips `jobPostingInfo.jobDescription` (HTML) to text.
  - **`http_post_json()`** added alongside `http_get()` (shared error-diagnostic path via `_do_request()`) — Workday's list endpoint is the only ATS here that requires POST.
- INSTALL.md egress allowlist now lists `*.myworkdayjobs.com` (covers every Workday pod).

### ⚠️ Pending live validation
Workday was in a scheduled maintenance window (`wd5` pod) when this shipped, so the CXS request/response contract is scaffolded from Workday's documented API, **not yet probed live**. Field names (`total`, `jobPostings[].externalPath`, `jobPostingInfo.jobDescription`) and the public-job-URL ↔ CXS-detail composition must be verified end-to-end against a live tenant before relying on Workday Favorites in production. Pure functions (`parse_workday_url`, canonical-URL construction) are unit-tested offline and correct.

## v0.1.14 — 2026-05-16

Runs rows now self-identify their plugin version. Post-mortem on the v0.1.13 503 work surfaced the gap: two re-fires (19:29, 21:57) both pre-dated the v0.1.13 diagnostic commit (22:17), so the diagnostic never ran — and the only way to know that was cross-referencing git commit timestamps against run `started_at`. A version stamped into the log removes that guesswork.

### Added
- **`jsonl_log` opens with a `meta` line carrying the plugin version.** `_app_version()` reads the repo-root `VERSION` file; `_build_jsonl_log()` emits it as the first JSONL line: `{"stage": "meta", "version": "0.1.14"}`. Zero Notion schema change — rides inside the existing `jsonl_log` property.

## v0.1.13 — 2026-05-15

Diagnostic patch for Ashby 503s observed from Cloud Routine container (Pavel's 5th production fire). Local probe from residential IP returns 200 OK on all boards; same calls from routine return HTTP 503 — strongly suggests Cloudflare WAF blocking the routine container's datacenter egress.

### Added
- **`http_get()` captures diagnostic context on HTTP errors.** Error string now includes the HTTP code, `cf-ray` header (Cloudflare edge identifier), `server` header, and a ≤120-char body snippet. This flows through to `jd_fetch_failed` rows and the Runs DB `jsonl_log`, so we can post-mortem an Ashby block from Notion alone next fire.

  Sample upgraded error string:
  ```
  http_403 | cf-ray=9fc4d1d46e7087a4-PRG | server=cloudflare | body='<!DOCTYPE html>...Ray ID...'
  ```

  vs. the v0.1.12 form:
  ```
  http_403
  ```

  Local probe of `api.ashbyhq.com` confirms `server=cloudflare` — the API is Cloudflare-fronted. Next routine fire will reveal whether the 503s are Cloudflare WAF (Error 1015 rate-limit, 1020 access-denied) or Ashby's own origin under stress, by examining the body snippet.

### Next steps
- If diagnostic confirms Cloudflare WAF block, v0.1.14 will switch Ashby fetch from API (`api.ashbyhq.com/posting-api/...`) to public board scrape (`jobs.ashbyhq.com/<slug>`). The Next.js page has the same `__NEXT_DATA__` content and is treated less aggressively by Cloudflare WAF.

## v0.1.12 — 2026-05-15

Pass A screener tightened — pursue_blockers + stretch_indicators now both drive hard drops at Pass A. Plus a resilience patch in `postjd_screen_apply` for when agents drop fields from their JSON output.

### Changed
- **`screener.md` agent spec rewritten with hard-drop rules.** Previously the spec mentioned `pursue_blockers` only in the keep-criterion bullet, and explicitly told the agent to *ignore* `stretch_indicators` ("those affect tier classification at Pass B, not screen-out"). The "When in doubt, prefer maybe over drop" rule trumped everything — visible eng-coder/sales-quota/HR title-pattern hits fell through to `maybe` and reached Opus.
  New structure: Step 1 hard-drop rules (pursue_blockers, stretch_indicators with clear title-pattern hits, learned_exclusions) fire BEFORE keep/maybe judgment. Step 2 (keep/maybe/drop) only applies when no hard rule fired. "Prefer maybe" restricted to title-relevance ambiguity, NOT visible blocker hits.
- **Dry-run validation** (3151 raw, Pavel's workspace): VC Pass A drops jumped from 220→234 with cleaner reasoning. All 234 audited as legit — every flagged "exception word" (Solutions Engineer / Implementation / etc.) turned out to be a different-blocker drop (e.g. Wiz Solutions Engineer Italy → Italian-language blocker fires separately). Net Pass B count dropped from 23 → 6 (4× reduction). Final fire cost projection: **~$3.40 total** (~$0.40 Haiku + ~$3 Opus) vs ~$70 in fire #3.

### Fixed
- **`postjd_screen_apply` positional-match fallback.** Dispatched Haiku agents sometimes omit `canonical_url` from their verdict JSON despite the spec requiring it. When the URL is missing, fall back to matching verdicts to scorer-input files positionally against the corresponding `favorites-postjd-batch-{N}.json`. Belt-and-suspenders so a single agent's output drift doesn't break the apply step.

## v0.1.11 — 2026-05-15

Haiku Pass A on post-JD Favorites — catches ambiguous-location cases the deterministic city-map misses.

### Added
- **Post-JD screener batches.** `jd_fetch_stage` now writes `favorites-postjd-batch-{N}.json` for each surviving Favorite after deterministic post-JD prefilter. Same shape as discovery-time screener batches.
- **`postjd_screen_apply` orchestrator stage.** Reads `postjd-verdicts-{N}.json` produced by the Haiku Pass A dispatch and deletes the corresponding `scorer-input-{idx}.json` for every `drop` verdict. Stops the Opus scorer from running on Favorites the Haiku judge already rejected.
- **`/fd-run` Step 3a.** New skill step between JD fetch and Pass B: dispatch screener on post-JD batches, then `postjd_screen_apply`.

### Changed
- **S1a Favorites policy loosened from strict positive-EU to detected-non-EU.** Same rule as VC sources now (drop only when detected country is unambiguously out-of-region; let "Remote/Anywhere/Global" through). Haiku handles the ambiguous cases via the new Step 3a — much better leverage than dropping unknowns deterministically.

### Cost preview on Pavel's workspace
- 741 Favorites auto-promoted at discovery
- 719 dropped by lax deterministic post-JD prefilter (detected non-EU)
- 22 → 2 post-JD Haiku batches (~$0.01)
- Haiku likely drops 15–18 (Seoul/Dubai/Toronto/Calgary remotes etc.) → ~4–7 reach Pass B
- Plus ~20 VC Pass A survivors → **total Pass B: ~25–30 calls = ~$7 Opus**

## v0.1.10 — 2026-05-15

Scorer can now `Drop` rows entirely instead of always tiering them — the v1.5 "hard exclusion means excluded, not Low" rule.

### Added
- **`Drop` tier on scorer agent.** Fourth verdict alongside Strong/Decent/Stretch. Use when the JD has an unambiguous hard pursue_blocker (language required, country-locked outside variant, hard coding requirement for non-coder). Agent spec includes Drop-vs-Stretch examples to disambiguate softer signals.
- **`write_stage` skips Drop rows.** Don't write to Tracker at all — no Match-quality flag, no Auto-excluded note, just absent. Persists to `dropped-by-scorer.json` for the JSONL log and Runs DB metric `dropped_by_scorer`.
- **`rescore_apply` closes Drop rows.** When a previously-tiered row gets re-scored as Drop, sets `Status=Closed` with `closed_at` populated. Why-fits preserves the scorer's reasoning prefixed `[Dropped by scorer]` so you can audit before the row falls out of default Tracker views.

### Changed
- `write_stage` printout now reports the Drop count: `"75 written, 0 failed; 0 Pursue, 2 Consider, 230 dropped by scorer (hard blockers)"`.

## v0.1.9 — 2026-05-15

S1a variant-region gate — closes the gap where remote-from-non-EU jobs leaked through to Pass B despite EU variant.

### Added
- **S1a prefilter stage (variant region).** Runs before S2. Hard-drops jobs whose detected country falls outside the variant's region set:
  - **EU variant**: 28 EU continental + EEA/EFTA countries. UK + Ireland conditional on `Profile.eu_include_uk_ie`.
  - **US variant**: United States only. No Canada/Mexico — keeps semantics tight; cross-border users can pin specific Favorites if they want.
  - Unknown country (e.g. "Anywhere", "Global", "North America") falls through; Pass B scorer handles it via residency check.
- Replaces the leaky behavior in S3 where `work_mode=remote` + `search_outside_home=True` kept jobs from ANY country. S3 still handles the home-country/relocation/work-mode logic, but only after S1a confirms the country is in scope.

### Effect on Pavel's workspace (dry-run, 3158 raw jobs)
- v0.1.8 (city-map + word boundary): 656 of 678 Favorites dropped post-JD = 22 survivors to Pass B
- v0.1.9 (+ S1a): **719 of 741 Favorites dropped** post-JD = 22 survivors (same Pass B count but now all are determinably-EU OR location-unknown, not remote-from-Seoul/Dubai/Toronto)
- Pass B candidates estimate: ~42 (≈$10–12 Opus)

## v0.1.8 — 2026-05-15

The v0.1.6 post-JD prefilter was right in shape but mostly inert because `_extract_country` couldn't read the location strings ATSes actually emit. Dry-run on Pavel's workspace showed post-filter dropping 63 of 742 Favorites (8.5%); after this fix, 656 of 678 drop (97%). Cost projection for next routine fire: **~$10–12 Pass B vs ~$70 in fire #3** (6x reduction).

### Fixed
- **`_extract_country` now recognizes 2-letter codes via word-boundary regex.** Previous substring match treated "houston" as containing "us" (false positive on Australia matching "AU" suffix elsewhere too). New logic uses `\b` boundaries and adds `US`, `IE`, `IL` to `_KNOWN_COUNTRIES` with aliases.
- **City-to-country map** added for the top US + EU + global tech hubs. ATS adapters (especially Ashby) commonly emit city-only values like "San Francisco" or "Berlin" without a country code. The map handles ~60 cities covering the bulk of AI-50 + VC portfolio jobs. Extensible — add a row when a new pattern shows up.

### Changed
- `Ashby` workplaceType "Hybrid"/"OnSite"/"Remote" now reliably maps to `work_mode` — this was already correct in v0.1.6 but only useful once country extraction caught up.

## v0.1.7 — 2026-05-15

Routine-debug visibility: populates `Runs.jsonl_log` so a failed Cloud Routine fire can be post-mortemed from Notion alone — no need to grab container logs.

### Added
- **`_build_jsonl_log()` in orchestrator** — at finalize time, aggregates the small/useful `/tmp/fd-run/<run_id>/` state files into a JSONL string: one line per stage (discovery, screener, jd_fetch, post_filter, write, finalize). Picks per-stage metrics, per-source counts, source-fetch errors, jd-fetch failure samples (top 20), post-filter drop samples (top 20), verdict-tier breakdown, top Pursue titles. Skips bulk files (candidates.json, all-verdicts.json, scorer-input-*) so the log stays compact. Typical size: 3–10 kB per fire.
- **`state/properties.to_text_chunked()`** — Notion rich_text caps individual chunks at 2000 chars. New helper splits at ≤1900-char boundaries so we can pack tens of kB into one property without hitting the API's per-chunk limit.

### Changed
- `state/runs.create()` jsonl_log cap raised from 10kB → 40kB (chunked write).
- `Runs.jsonl_log` column on existing Notion DBs will start populating from the next fire.

## v0.1.6 — 2026-05-15

Cut Favorites Pass B cost dramatically by re-applying deterministic prefilter after JD fetch enriches them.

### Added
- **Post-JD prefilter for Favorites.** `jd_fetch_stage` now re-runs the S2–S9 prefilter on every Favorites candidate after enrichment. Discovery couldn't apply it (Favorites arrive with title/location/work_mode/salary blank), but post-JD-fetch those fields are populated — same deterministic checks VC candidates passed at discovery. Drops non-CZ hybrid/onsite Favorites, salary-floor failures, etc. before they hit the Opus scorer. Estimated impact at Pavel's current profile: ~285 Favorites → ~30–50 surviving post-filter, saving roughly $50–60 per fire on Pass B. Dropped rows persist in `post-filter-dropped.json` with the rejecting fields visible.
- **Salary extraction from Greenhouse + Lever JDs.** Greenhouse's `pay_input_ranges` (yearly cents) and Lever's `salaryRange` (with hour→year conversion when interval=hour) are now parsed into `salary_min_yearly` / `salary_max_yearly` / `salary_currency`, feeding both S9 prefilter and the Salary column in Tracker. Ashby doesn't expose structured salary; skipped there.

### Changed
- `jd_fetch_stage` printout includes the post-filter-dropped count alongside JDs-ok and JDs-failed.

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
