# Funded Drop — install & Cloud Routine setup

Two-layer install:

- **§1–§3 (local)**: clone the repo, get a Notion integration token, run `/fd-setup` to create your 4 Notion DBs + Profile row. Required for *anyone*. Once done locally, you can already fire `/fd-run` manually whenever you want.
- **§4 (optional)**: wire up Claude Code's Cloud Routine to fire `/fd-run` on a weekly schedule unattended. Builds on §1–§3.

If something fails, see [§5 Troubleshooting](#5-troubleshooting).

## 1. Prerequisites

- **Python 3.9+** — `python3 --version` to check.
- **Git** — to clone the repo.
- **Claude Code** — install from <https://claude.com/code>. Pro subscription recommended (the Pass B scorer uses Opus).
- **Notion account** — free tier works fine. You need permission to create databases under a page.

## 2. Get the repo

```bash
git clone https://github.com/pavel-vibe-code/funded-jobs-drop.git
cd funded-jobs-drop
```

No dependencies to install — Funded Drop runs on Python stdlib only.

## 3. First-time setup (local, interactive)

This is a one-time bootstrap per user. Takes ~5 minutes.

### 3.1 — Mint a Notion integration token

1. Go to <https://www.notion.so/profile/integrations>.
2. Click **New integration** → **Internal** integration.
3. Name it anything (e.g. "Funded Drop").
4. **Capabilities**: grant Read content, Update content, Insert content. Comments + user info optional.
5. Click **Save**. Copy the **Internal Integration Token** — starts with `ntn_` or `secret_`. Keep it private.

### 3.2 — Create a parent page in Notion

This is where Funded Drop will create its 4 databases.

1. In Notion, create a new page anywhere in your workspace. Call it **Funded Drop** (or anything — name doesn't matter).
2. Open the page → click the **`···`** menu top-right → **Connections** → search for the integration you just created → **Add**.
3. Copy the page URL. The URL looks like `https://www.notion.so/Funded-Drop-<32-hex>?...`. You'll paste this URL into the wizard.

**Why a fresh page**: Funded Drop creates 4 databases under this parent. Putting them under a dedicated page keeps your existing Notion workspace clean. If you've used the v1.5 ai50-job-search plugin and want to reuse that workspace, create the Funded Drop page as a child of the v1.5 parent — Notion inherits sharing automatically through page nesting.

### 3.3 — Run `/fd-setup`

In the repo directory:

```bash
claude
```

Then in Claude Code:

```
/fd-setup
```

The wizard asks ~14 questions in 6 sections. Allow ~5 minutes.

| Section | Fields | Notes |
|---|---|---|
| **Notion access** | token + parent page URL | Paste from §3.1 + §3.2 |
| **Region & location** | variant (EU/US), eu_include_uk_ie, home country/city/state | Variant determines which VC subdomain regions to query |
| **Work mode & relocation** | work_modes (Remote/Hybrid/Onsite), search_outside_home, willing_to_relocate | Combined with country to drive the S3 prefilter |
| **Seniority & salary** | accepted_seniority (entry/mid/senior/staff/principal/executive), salary_floor amount + currency | Salary filter only fires when disclosed — undisclosed jobs always pass S9 |
| **Scoring criteria** (3 free-text fields, the most important section) | `interest_description`, `pursue_blockers`, `stretch_indicators` | These shape the Pass B scorer's tier decisions. Spend a minute per field. They iterate over time as you give Tracker feedback. |
| **CV** (optional but recommended) | cv_url, cv_summary | The scorer reads `cv_summary` as part of fit judgment |
| **System settings** | posted_since_window, ai50_seed_enabled, webhook_url, webhook_enabled | Window: 1 week / 2 weeks / 1 month. AI-50 seed: 14 extra companies. Webhook: Slack/Discord/Teams/Zapier URL for new Strong matches. |

### 3.4 — Verify setup completed

After the wizard reports success:

```bash
# Look at your settings.local.json
cat ~/.claude/settings.local.json
```

You should see a `funded-drop` key with `notion_token`, `parent_page_id`, and four DB IDs (`tracker_db_id`, `profile_db_id`, `favorites_db_id`, `runs_db_id`).

In Notion, navigate to your parent page — you'll see 4 child databases: **Tracker**, **Profile**, **Favorites**, **Runs**. Profile has exactly one row (your settings). The others are empty until your first fire.

### 3.5 — First test fire (local)

```
/fd-run
```

This invokes the full pipeline against your real workspace. It will:

1. Fetch ~2,500 raw jobs from 11 VC sources (Greylock typically has only a handful).
2. Apply your prefilters (S2–S9). For a Czechia-based profile with strict country rules, ~80% drop here.
3. Dispatch Pass A screener agents in parallel waves (~5–8 batches of 15 candidates each).
4. Fetch JDs for screener survivors (~30 in a typical fire).
5. Dispatch Pass B scorer agents in parallel.
6. Write evaluated rows to Tracker DB.
7. Write Runs DB row with the summary.
8. POST webhook if you configured one AND there are new Strong matches.

Wall-clock: ~5 minutes. Cost: ~$7–8 in LLM (see [README cost guide](./README.md#cost-guide)).

When it finishes you'll see `finalize_stage: Runs row written (<page_id>); webhook: <status>; N Pursue rows`. Open Tracker in Notion to review.

If something fails mid-fire, the orchestrator is resilient: each stage is bounded-retry, missing inputs degrade gracefully to "no data this stage," and finalize always writes a Runs row even if upstream stages crashed. The Runs row's `summary` + `errors_summary` columns tell you what happened.

## 4. Cloud Routine setup (optional)

A Cloud Routine fires `/fd-run` on a schedule (e.g. every Sunday 21:00 your TZ) in a sandboxed container that does NOT need your laptop running.

### 4.0 — How a Routine actually runs

A Cloud Routine container:

1. **Clones your repo from GitHub.** Whatever's on `main` at fire time gets pulled fresh into the container. Code changes you push land in the next fire automatically — no separate deploy step.
2. **Runs your setup script** (§4.4). Quick — just verifies the plugin root.
3. **Loads the agent runtime + your environment.** Custom env vars (`FD_NOTION_TOKEN`, `FD_PARENT_PAGE_ID`) and the allowed-domains list become visible *here*. The setup script CANNOT see custom env vars — they live only in the agent-runtime context.
4. **Executes the trigger prompt** — runs `/fd-run` end-to-end with no human in the loop.
5. **Tears down.** Nothing on the container's filesystem survives. Your Notion workspace is the only state that persists.

The repo ships `.claude/settings.json` with the permission allowlist (Bash + Agent + Read/Write patterns). The Routine clones this and applies it — you don't have to write it yourself.

### 4.1 — GitHub access prereq

Routines clone via your claude.ai account's GitHub connection. Private repos require the connection to have read access. Verify at <https://claude.ai/settings>. If it's not connected, run `/web-setup` in Claude Code to do the OAuth flow.

### 4.2 — Create a Routine environment

Go to <https://claude.ai/code> → **Settings → Environments → New environment**. Name it e.g. `funded-drop-prod`.

#### 4.2a — Environment variables

In the **Environment variables** field (`.env` format, one `KEY=value` per line):

```text
FD_NOTION_TOKEN=ntn_<your-token-from-§3.1>
FD_PARENT_PAGE_ID=<32-char-page-id-from-§3.2-URL>
```

That's it — **only two variables required**. At fire time, `load_workspace()` queries the children of the parent page and finds the 4 DBs by their canonical titles (`Tracker`, `Profile`, `Favorites`, `Runs`). Same UX as v1.5.

To extract the parent page ID: the URL is `https://www.notion.so/Funded-Drop-36036f7666be80cda885d563c785ccbb?source=copy_link`. The 32-char hex (`36036f7666be80cda885d563c785ccbb`) is the ID. With or without hyphens both work.

**Optional escape hatch** — if you ever rename a DB in Notion UI (breaking the canonical-title lookup), override per-DB:

```text
FD_TRACKER_DB_ID=<32-hex>
FD_PROFILE_DB_ID=<32-hex>
FD_FAVORITES_DB_ID=<32-hex>
FD_RUNS_DB_ID=<32-hex>
```

All four must be present together to skip discovery.

**Security note**: Notion warns that environment variables are visible to anyone with edit access on the environment. For personal accounts where only you have access, that's fine. For shared accounts, rotate the token regularly or don't share the environment.

#### 4.2b — Allowed domains

The Routine container has restricted egress. Allowlist these hosts:

```text
api.notion.com
*.greenhouse.io
boards-api.greenhouse.io
boards-api.eu.greenhouse.io
*.ashbyhq.com
api.ashbyhq.com
jobs.lever.co
api.lever.co
jobs.a16z.com
jobs.sequoiacap.com
jobs.greylock.com
jobs.lsvp.com
jobs.bvp.com
jobs.kleinerperkins.com
careers.balderton.com
jobs.accel.com
jobs.generalcatalyst.com
careers.atomico.com
indexventures.getro.com
jobs.insightpartners.com
```

| Host(s) | Used by |
|---|---|
| `api.notion.com` | All Notion reads + writes |
| `*.greenhouse.io` + `boards-api.*` | Greenhouse JD fetch (classic + EU data residency) |
| `*.ashbyhq.com` + `api.ashbyhq.com` | Ashby JD fetch (list-endpoint pattern) |
| `jobs.lever.co` + `api.lever.co` | Lever JD fetch |
| 7× `jobs.<vc>.com` and similar | Consider portfolio listing pages (1 per VC) |
| 5× Getro hosts | Getro portfolio listing pages |

The page-scrape fallback (which recovers wiz.io / bolt.eu / scrive.com etc. via deterministic HTML cleaning) needs arbitrary outbound HTTPS — not listable here. Those will fail under restricted egress; affected rows land as `Status: jd_fetch_failed` for manual review. That's an acceptable degradation — the deterministic-ATS path covers ~80% of jobs out of the box, and `/fd-rescore failed` can later pick up the rest if you ever loosen egress.

**No `api.anthropic.com`.** All LLM work runs through Claude Code agents (screener, scorer, summarize, qa). They bill against your Claude subscription quota, not via an Anthropic API key.

#### 4.2c — Setup script

In the **Setup script** field, paste this:

```bash
# Find the plugin root in the container (~1s on find).
ORCH=$(find / -name "orchestrator.py" -path "*/funded-jobs-drop/*" -type f 2>/dev/null | head -1)
PLUGIN_ROOT=$(dirname "$ORCH")
echo "Setup OK; plugin root: $PLUGIN_ROOT"
```

That's it. **Do not** try to materialize `~/.claude/settings.local.json` from env vars here — the setup-script context can't see custom env vars (only Claude-cloud and system vars). The orchestrator reads `FD_*` env vars at agent-runtime via `state/config.py:load_workspace()`. The setup script's only job is to verify the repo was cloned.

> **Why no auth pre-check.** Earlier we tried `python3 -c "from state.profile import read; read()"` here. That always failed because `FD_NOTION_TOKEN` isn't visible in the setup context. The auth check happens implicitly the moment `discovery_stage` makes its first Notion call — same loud failure, just a few seconds later.

### 4.3 — Create the Routine

Go to <https://claude.ai/code> → **Routines → New Routine**.

| Field | Value |
|---|---|
| **Repository** | `pavel-vibe-code/funded-jobs-drop` (or your fork if working from one) |
| **Environment** | the one you created in §4.2 |
| **Schedule** | weekly. Recommended: Sunday 21:00 your TZ (Tracker is fresh Monday morning) |
| **Trigger / prompt** | (paste verbatim below) |

**Trigger prompt** — paste verbatim:

```text
Run /fd-run.

Routine context (no human in the loop):
- Auth + DB IDs: read from FD_* environment variables (FD_NOTION_TOKEN +
  FD_PARENT_PAGE_ID minimum; FD_*_DB_ID optionally to skip discovery).
- Do not ask interactive questions. If something is ambiguous, pick the
  documented default. If genuinely blocked, fail loudly and exit non-zero.
- Bounded retry only — one retry per failed agent dispatch, then proceed.
- Webhook posts only if Profile.webhook_enabled is true AND there are
  Strong-Pursue rows in this fire.

Then execute the /fd-run skill end-to-end and print the canonical run summary
from /tmp/fd-run/<run_id>/finalize-result.json.
```

> **Tip — CLI alternative.** You can create the Routine from inside Claude Code instead of the web UI: run `/schedule` and walk through the prompts. Same backend; useful if you want to script it alongside other terminal work.

### 4.4 — Test-fire the Routine

In the Routine UI, click **Run now**. Watch the logs. A successful run looks like:

```text
Setup OK; plugin root: /workspace/funded-jobs-drop
=== orchestrator.discovery · run_id=<8hex> ===
  closure: 0 marked Closed, healthy sources: ['Accel', 'Atomico', 'Balderton', ...]
discovery_stage: 2497 raw → 319 survivors → 22 screener batches
... (screener agent dispatches, ~30s each in parallel waves)
=== orchestrator.aggregate · run_id=<8hex> ===
screener_aggregate: 319 evaluated → 39 survivors, 280 dropped
=== orchestrator.jd_fetch · run_id=<8hex> ===
jd_fetch_stage: 27 JDs ok, 12 failed
... (scorer agent dispatches in parallel)
=== orchestrator.write · run_id=<8hex> ===
write_stage: 39 written, 0 failed; 0 Pursue, 2 Consider
... (summarize agent)
=== orchestrator.finalize · run_id=<8hex> ===
finalize_stage: Runs row written (<page_id>); webhook: skipped; 0 Pursue rows
```

If you see `AuthError: FD_NOTION_TOKEN is set but FD_PARENT_PAGE_ID is missing` — fix the env vars, re-fire.

If you see `Agent type 'screener' not found` — the skills directory layout is wrong (must be `.claude/skills/<name>/SKILL.md`, not flat `.md`). v0.1.2+ fixed this; if your fork is older, pull `main`.

If a Bash command stalls without a permission prompt — the allowlist in `.claude/settings.json` doesn't cover one of the patterns. Check the repo's settings file matches what the skill scripts invoke.

### 4.5 — Maintenance

- **Token rotation**: mint a new integration token (§3.1), share the existing parent page with it (§3.2.2), update `FD_NOTION_TOKEN` in the Routine env, revoke the old token. No code change needed.
- **Schema repair**: if a future release adds a column to one of the 4 DBs, run `/fd-setup --repair` locally. It patches missing properties in-place without recreating DBs or losing data.
- **Re-create from scratch**: archive the existing Funded Drop parent page in Notion (`···` → Move to Trash), then run `/fd-setup --rewipe`. Refuses unless the parent is actually archived/gone (verified via Notion API).
- **Profile tweaks**: `/fd-settings` for conversational edits. Re-firing `/fd-run` immediately after picks up the new profile_hash, but only NEW candidates feel the change. For existing Tracker rows, run `/fd-rescore stale` — re-evaluates rows whose `profile_hash_at_eval` no longer matches.
- **Feedback loop**: as you flip `Match quality` to `Feedback` (or back to `OK`) on Tracker rows, run `/fd-recycle-feedback`. The qa agent reads your overrides + auto-feedback notes, refines `Profile.learned_exclusions` and `learned_examples`. Future fires apply the refined rules at Pass A + Pass B.

## 5. Troubleshooting

### `AuthError: Settings file not found at … and no FD_* env vars set`

You haven't run `/fd-setup` locally yet (or haven't configured the Routine env). For local use, run `/fd-setup`. For Routine, set `FD_NOTION_TOKEN` + `FD_PARENT_PAGE_ID` in the env config (§4.2a).

### `SetupError: Workspace already configured (parent_page_id=…)`

You ran plain `/fd-setup` on a workspace that's already set up. Use:
- `/fd-setup --repair` to patch missing schema columns.
- `/fd-settings` to edit profile fields.
- `/fd-setup --rewipe` to start from scratch (archive the Notion page first).

### `SetupError: --rewipe requires the existing parent page (...) to be archived in Notion first`

Open the Funded Drop page in Notion → `···` menu → Move to Trash. Then re-run `/fd-setup --rewipe`.

### `SetupError: DB titled 'Tracker' not found under parent page <id>`

The parent page exists but one of the 4 canonical DBs is missing. Either the DB was deleted manually, or the wizard didn't finish. Run `/fd-setup --repair` — it'll list which DBs are missing.

### `NotionError: HTTP 401`

The token is wrong, revoked, or doesn't have access to the parent page. Re-check §3.1 + §3.2. For Routine, verify `FD_NOTION_TOKEN` matches what you have in `~/.claude/settings.local.json` (or whatever current token you've shared with the parent page).

### `NotionError: HTTP 404 on POST /v1/databases`

The integration doesn't have access to the parent page. In Notion: open the page → `···` → Connections → search for your integration → Add.

### `Agent type 'screener' not found`

Skills directory layout issue. Fixed in v0.1.2 — pull `main` from GitHub and re-fire.

### Many `jd_fetch_failed` rows in Tracker

Most likely cause: the candidate URL points to a custom careers page (Revolut, Bolt, custom-domain Greenhouse SPAs) and the page-scrape fallback can't recover it under Routine's restricted egress. Run `/fd-rescore failed` locally (where outbound HTTPS is unrestricted) to recover those rows.

### `socket.timeout: The read operation timed out`

Transient. Re-fire. If it persists for the same source, that VC's API may be down — wait an hour and retry.

### Cloud Routine fire times out before finalize

A single scorer agent dispatch is taking too long (Opus latency under load). The pipeline is resilient — already-completed scorer outputs are picked up by `rescore_apply` if you re-fire with the same run_id. Alternative: reduce screener-survivor count by tightening `pursue_blockers` (less Pass B work per fire).

### Webhook test from `/fd-test-webhook` returns `HTTP 4xx`

The webhook URL is invalid, revoked, or rejected the payload. Re-create the webhook in Slack/Discord/Zapier and update via `/fd-settings`. Test again before relying on it for production fires.

### Want to read the JSONL log from a failed fire

Routine logs surface in the claude.ai/code Routine detail page. The Runs row in Notion also carries a (capped 10kB) JSONL snapshot in the `jsonl_log` field. Full logs only persist on local fires (containers are torn down).

## 6. Where to go next

- [README.md](./README.md) — high-level pitch + design highlights
- [CLAUDE.md](./CLAUDE.md) — dev-facing design principles + module map
- [CHANGELOG.md](./CHANGELOG.md) — release history
- Source — module structure under `discovery/`, `evaluation/`, `state/`, `notify/`, `setup/`, plus `orchestrator.py` and `recycle_feedback.py` at the repo root
