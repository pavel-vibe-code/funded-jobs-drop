# Funded Drop — design principles

This is a clean-break v2 of the parent project `ai50-job-search`. Selectively reuses proven pieces, fresh rewrite of the rest. Built on lessons documented in the parent's memory.

## Pillars

1. **Fully automated.** No manual config steps anywhere. No "paste a token per fire." If a path requires human intervention, it's the wrong path — push harder for an automated alternative (headless browser, OAuth flow, etc.) before accepting manual fallbacks.

2. **Deterministic before LLM.** Every filter that can be a Python `if` is a Python `if`. LLM only for genuine judgment (Pass A inclusion screening, Pass B scoring, summary writing, QA learning).

3. **Spec-driven.** Implement what's requested, not what might be useful. No future-proofing for hypothetical features. Three similar lines is better than a premature abstraction.

4. **Simplicity over cleverness.** No abstractions beyond what the spec requires. The simplest version that meets the spec is the right one. Don't pad with defensive checks at internal boundaries.

5. **Efficiency.** Minimize HTTP calls, LLM tokens, dependencies, wall time. State the cost when proposing an approach. Every fire records its LLM cost in the Runs DB so users see what each run costs.

6. **One responsibility per module.** Target: ~6 modules total. If a module's purpose can't be stated in one sentence, it's two modules.

## Module map

```
discovery/   Deterministic: fetch jobs from VC sources, dedup, apply filters
evaluation/  Two-pass LLM: screener (Pass A, Haiku) → scorer (Pass B, Opus)
state/       Notion I/O: read profile/favorites/tracker, write evaluated rows
notify/      Per-fire summary to Runs DB, webhook push on new Strong matches
setup/       Wizard + Notion workspace creation, idempotent / repairable
config/      Static data: VC roster, AI-50 supplement seed
```

## Agents (4 pipeline + 5 dev/test)

**Pipeline (runtime):**
- `screener` — Pass A, Haiku, batched triage on structured data
- `scorer` — Pass B, Opus, per-job full JD evaluation
- `summarize` — composes the per-fire summary text
- `qa` — instructor mode, refines LLM instructions from user feedback

**Dev/test (build-time):**
- `code-reviewer` — Python-savvy reviewer, principles compliance
- `test-smoke`, `test-dryrun`, `test-unit` — parallel test runners
- `test-orchestrator` — aggregates parallel test results, blocks phase advance on red

## Notion workspace

```
Portfolio (user-named parent page)
├── Tracker          — evaluated jobs (the primary user surface)
├── Profile          — single-row DB of user preferences
├── Favorites        — pinned companies (bypasses VC discovery)
└── Runs             — per-fire summary + (hidden) audit fields
```

State storage: Notion is the single source of truth. No SQLite, no chunked-JSON page, no local cache across fires. Tracker IS the state.

## Lessons applied from the parent (`ai50-job-search`)

- Notion API version pinned (avoids SDK drift)
- Single `data_source` count check on every DB at fire start (catches stray UI clicks that broke v1.5)
- Schema introspection at read time — missing column → `SetupError` with repair guidance, never silent fallback
- Setup wizard idempotency — re-running detects existing DBs, patches missing columns, preserves data
- Token tracking ported from v3.0.5 — per-fire cost accounting
- MCP vs direct Notion API path duality preserved
- Two-repo release strategy: private until v0.1.0, public main + tags after

## Cloud Routine compatibility (HARD CONSTRAINT)

This plugin must run in Claude Code's Cloud Routine mode: a fresh container per fire with **no persistent filesystem between fires**. Anything written to disk during a fire is gone when it ends. Notion is the only place state survives.

**Rules every new module must follow:**

- **No SQLite, no JSON state files, no chunked-storage hacks between fires.** Tracker DB IS the state — there is no second source of truth.
- **`/tmp/fd-run/{run_id}/` is per-fire ephemeral.** Use freely for inter-stage handoff within one fire; assume it vanishes after.
- **JSONL run log lands in Notion** (`Runs.jsonl_log` text column, capped at ~10kB). On local-mode dev runs, additionally write to disk for inspection. Notion is canonical.
- **`settings.local.json`** holds the auth token + DB IDs. In cloud routine, this is mounted at agent runtime startup (see parent's `project_routine_env_var_quirk`: setup-script context can't see custom env vars; only agent runtime does). Don't write back to it during fires — only setup writes.
- **Cost tracking** accumulates per-fire in memory, lands in `Runs.cost_usd`. Don't try to sum across fires locally; query Notion if needed.
- **Don't cache anything across fires.** First action of a fire is read from Notion; last action writes to Notion. In-fire memory caching is fine.

If a feature seems to need persistent disk state across fires, that's a signal to redesign — push it into Notion or accept that it's a local-only convenience that won't work in production.

## What was deliberately NOT brought from parent

- `fetch-and-diff.py` (2,243 lines) → replaced by `discovery/runner.py` (~300 LOC target)
- `build-state-chunks.py` — obsolete; no state-page chunking in v2
- 7-region taxonomy with dynamic score table → replaced by 2 variant presets
- AI-50 curated companies list → replaced by VC portfolio discovery + 14-company AI-50 supplement
- Multiple agents (`search-roles`, `notify-hot`, `validate-urls`, `compile-write`) → replaced by deterministic Python
- Multi-page Profile hierarchy in Notion → replaced by 1-row Profile DB

## Slash commands

- `/fd-info` — list all commands
- `/fd-setup` — first-time wizard
- `/fd-run` — manual fire trigger
- `/fd-settings` — edit profile / system settings
- `/fd-recycle-feedback` — invoke qa to refine LLM instructions
- `/fd-rescore` — manual rescore of stale tracker rows
- `/fd-test-webhook` — validate webhook config
