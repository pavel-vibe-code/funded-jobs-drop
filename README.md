# Funded Drop

Automated weekly job search across VC-backed companies. Pulls from 12 top-tier VC portfolios (~2,300 companies), filters deterministically against your profile, scores with two-tier LLM evaluation, writes results to your Notion workspace, and pushes new matches to your configured webhook (Slack / Discord / Teams / Zapier / anywhere).

Built as a Claude Code plugin. Runs on your machine via `claude` CLI, or unattended in Claude Code's Cloud Routine.

**Status**: v0.1.2 — internal alpha. Public release pending.

```text
                ┌──────────────────────────────────────────────────┐
                │   Funded Drop fire (weekly, ~5 min, ~$8 LLM)     │
                └──────────────────────────────────────────────────┘
                                       │
   ┌─ Discovery ──────────────────────────────────────────────────┐
   │  7 Consider VCs + 5 Getro VCs + your Favorites + AI-50 seed  │
   │  → dedup → S2-S9 prefilter (work mode, country, seniority,   │
   │            salary floor, industry/company exclusions)        │
   └──────────────────────────────────────────────────────────────┘
                                       │
   ┌─ Evaluation ─────────────────────────────────────────────────┐
   │  Pass A: screener (Haiku, batched, structured tags only)     │
   │  Pass B: scorer  (Opus, per-job, full JD)                    │
   │  → Strong / Decent / Stretch tier  + auto-exclude flag       │
   └──────────────────────────────────────────────────────────────┘
                                       │
   ┌─ State ──────────────────────────────────────────────────────┐
   │  Notion Tracker DB (the single source of truth)              │
   │  + Runs DB (per-fire summary, cost, errors)                  │
   │  + Profile DB (your prefs, qa-learned rules)                 │
   │  + Favorites DB (pinned companies + AI-50 seed)              │
   └──────────────────────────────────────────────────────────────┘
                                       │
   ┌─ Notify ─────────────────────────────────────────────────────┐
   │  Webhook POST on new Strong matches (Slack/Discord/etc.)     │
   └──────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
git clone https://github.com/pavel-vibe-code/funded-jobs-drop.git
cd funded-jobs-drop
claude
# In Claude Code:
/fd-setup    # interactive wizard, ~5 minutes
/fd-run      # first fire, ~5 minutes wall + ~$8 LLM
```

Full setup including Cloud Routine scheduling → [INSTALL.md](./INSTALL.md).

## Coverage

| Source | Companies | Style |
|---|---|---|
| **7 Consider-platform VCs** | ~1,400 companies | a16z, Sequoia, Greylock, Lightspeed, Bessemer, Kleiner Perkins, Balderton |
| **5 Getro-platform VCs** | ~900 companies | Accel, General Catalyst, Atomico, Index, Insight Partners |
| **AI-50 supplement** (opt-in) | 14 AI-leading companies | Cohere, Cognition, Crusoe, HeyGen, Krea, Listen Labs, OpenEvidence, Rogo, Runway, SambaNova, SSI, Surge AI, World Labs, Clay |
| **Favorites** | unlimited | Pin individual companies via direct ATS adapters (Greenhouse, Ashby, Lever, Workday, TeamTailor, Comeet, Recruitee, Personio, BambooHR, SmartRecruiters, Workable, Homerun) |

Total addressable surface: ~2,300 unique portfolio companies. After deterministic prefilter (region / work mode / seniority / etc.), a typical fire surfaces 200–400 candidates → screener thins to 30–60 → scorer evaluates each in full → ~5–20 land in your Tracker as Strong/Decent/Stretch.

## What ends up in Notion

Four databases under a parent page you name (recommended: "Funded Drop" inside your existing workspace):

- **Tracker** — every evaluated job. Your primary surface. Filter by Match (Strong/Decent/Stretch), Status (New/Saved/Closed/jd_fetch_failed), or Match quality (OK/Feedback). The `Why fits` column carries scorer reasoning grounded in your profile.
- **Profile** — single-row DB holding your search prefs (variant, location, work modes, seniority, salary floor) and the free-text fields that shape scoring (`interest_description`, `pursue_blockers`, `stretch_indicators`). Edit via `/fd-settings`.
- **Favorites** — pinned companies, including the 14-company AI-50 seed if enabled.
- **Runs** — one row per fire. Summary, cost, per-stage counts, errors, JSONL log.

Tracker is the **single source of truth**. No SQLite, no local state files, no chunked-storage hacks. The plugin's container is ephemeral per fire; everything that survives lives in Notion.

## Two-tier LLM evaluation

The scoring rubric is shaped by **your** profile, not a hardcoded taxonomy. Three free-text fields drive it:

- `interest_description` — what kinds of roles you want. Paragraphs are fine.
- `pursue_blockers` — disqualifying patterns. "Mandatory evenings." "US-only territories." "Coding as a hard requirement."
- `stretch_indicators` — partial-fit signals. "Vague title," "<30 people," "industry mismatch."

The qa learning loop (`/fd-recycle-feedback`) reads your Match-quality edits in Tracker and refines `learned_exclusions` + `learned_examples` over time. Auto-exclude flag closes the loop: when the scorer detects a `pursue_blocker`, the row writes `Match quality = Feedback` + `[Auto]`-prefixed note. If you flip that back to OK, that's a strong "over-block" signal qa picks up next cycle.

## Cost guide

Per-fire LLM cost (real numbers from the v0.1.12 dry-run against 3,151 raw jobs, ~983 prefilter survivors, on a strict EU-only Director-track profile):

| Stage | Model | Calls × tokens | Cost |
|---|---|---|---|
| Pass A on VC batches | Haiku | ~17 × 37k | ~$0.20 |
| Pass A on post-JD Favorites | Haiku | ~2 × 26k | ~$0.01 |
| Pass B scorer | Opus | ~6 × 26k | ~$3 |
| Summary | Sonnet | 1 × 20k | ~$0.05 |
| **Per fire** | | | **~$3-4** |
| **Per month** (weekly) | | | **~$15-20** |

The scorer dominates per-call but the volume is small after two-stage filtering: deterministic prefilter (S1a variant region, S2-S9) plus tightened Pass A screener (hard-drops on pursue_blockers + stretch_indicators) collapse ~3,000 raw → ~6-10 reaching Opus. Tightening `pursue_blockers` further is the biggest cost lever.

## Slash commands

All commands run via `claude` CLI in this repo directory (locally) or fire from a Cloud Routine (unattended).

| Command | Purpose |
|---|---|
| `/fd-setup` | First-time setup wizard. Creates 4 Notion DBs + Profile row. `--repair` to patch schema, `--rewipe` to re-create after archiving existing page. |
| `/fd-run` | Single pipeline fire. ~5 min wall, ~$8 LLM. |
| `/fd-rescore [mode]` | Pass B re-evaluation of existing rows. Modes: `failed` (retry jd_fetch_failed), `stale` (profile drifted), `flagged` (user marked Match quality). |
| `/fd-recycle-feedback` | Learning loop. Reads your Tracker feedback, updates `Profile.learned_*` so next fire applies them. |
| `/fd-settings` | Edit Profile fields conversationally. |
| `/fd-test-webhook` | POST a test message to verify wiring. |
| `/fd-info` | List all commands. |

## Design principles

5-pillar dev-facing companion in [CLAUDE.md](./CLAUDE.md):

1. **Fully automated** — no manual config steps anywhere. No "paste a token per fire."
2. **Deterministic before LLM** — every filter that can be a Python `if` is. LLM only for genuine judgment.
3. **Spec-driven** — implement what's requested, not what might be useful.
4. **Simplicity over cleverness** — three similar lines is better than a premature abstraction.
5. **Efficiency** — minimize HTTP calls, LLM tokens, dependencies, wall time. Every fire records its cost.

## Cloud Routine constraints

This plugin is built to run unattended in Claude Code's Cloud Routine: fresh container per fire, no persistent filesystem between fires. Rules every module follows:

- **No SQLite, no JSON state files, no chunked-storage hacks between fires.** Tracker IS the state.
- `/tmp/fd-run/<run_id>/` is per-fire ephemeral. Used freely for inter-stage handoff within one fire; vanishes after.
- `~/.claude/settings.local.json` (local laptop) or `FD_NOTION_TOKEN` + `FD_PARENT_PAGE_ID` env vars (Cloud Routine) supply auth. Setup-script context can't see custom env vars, so config loads at agent-runtime, not setup-script time.

## Requirements

- Python 3.9+
- Claude Code (subscription — Pro or higher recommended for the Opus scorer)
- Notion account + integration token

## Status

- ✅ End-to-end fire validated against real Notion + real VC APIs + real LLM agents
- ✅ Cloud Routine setup documented in [INSTALL.md](./INSTALL.md)
- ⚠️ Webhook tested with payload formatting; live Slack/Discord/etc. integration is the user's first-fire test
- ⚠️ No public release yet — repo is private until v0.2 (after broader testing)

See [CHANGELOG.md](./CHANGELOG.md) for release history.

## License

TBD. Repo is private during alpha; license selection happens at first public release.
