# Funded Drop Beta — Spec

A multi-tenant beta of Funded Drop. Lets people **without Claude Code** test the
job-search pipeline through a friendly Notion-form interface. The operator (you)
runs every fire on your own Claude Code routine budget and absorbs all LLM cost,
in exchange for tester feedback.

This is a **separate repo** (`funded-drop-beta`), forked from `funded-jobs-drop`.
The pipeline (discovery → evaluation → state → notify) is reused almost verbatim;
the new work is a multi-tenant control layer wrapped around it.

---

## 1. Background

`funded-jobs-drop` is single-tenant: one Profile, one Tracker, one operator. This
beta makes it serve N testers from one operator account. Three decisions, already
made, shape everything below:

| Decision | Choice |
|----------|--------|
| Tester interface | **Notion forms** — no hosted frontend, no backend |
| Run trigger | **Batch daily** — one routine drains a queue, processes all testers |
| Notion tenancy | **Per-tester 4-DB sets** under the operator's Notion |

The single most important fact from the 10-run data analysis: a tester's **first
fire** Opus-scores 20–30× the volume of a steady-state fire (cold Tracker → nothing
to dedup against) and yields ~nothing useful. Cold start is the dominant cost and
must be capped explicitly.

## 2. Goals / Non-goals

**Goals**
- A tester signs up and requests runs without ever touching Claude Code.
- Every tester gets isolated, real, scored results — not placeholder data.
- Per-tester and total cost are *measured* (cost tracking, which today is a stub).
- Cost per tester is bounded and predictable: cold start capped, ongoing capped.
- One operator routine run/day serves the whole beta.

**Non-goals (for the beta)**
- No hosted web app, no custom auth, no billing.
- No real-time / on-demand runs — next-morning results are acceptable.
- No cross-fire local state — Notion remains the only state (CLAUDE.md hard rule).
- Not untied from Claude Code yet — see §16 for the forward path.

## 3. Roles

- **Operator** — owns the Notion workspace, the Anthropic spend, and the routine
  budget (15 runs/day). Onboards testers, watches cost.
- **Tester** — a no-Claude user. Interacts only with two Notion forms and a
  read-only link to their own Tracker.

## 4. Interface — Notion forms

Three surfaces, all Notion-native. Zero code to host.

### 4.1 Signup form
A Notion form bound to the **Testers DB**. Collects everything `/fd-setup` would
ask interactively:
- Email (identity key), name
- Variant (EU / US / …), home country, work modes, willing-to-relocate
- Accepted seniority, scoring criteria / pursue-blockers / stretch-indicators
- CV (paste or file)
- Up to **3** favorite companies (the cap — see §11)
- **Mandatory consent checkbox**: see §15

Submitting creates a Testers row with `status = pending_setup`.

### 4.2 Run-request form
A Notion form bound to the **Run Queue DB**. One shared form for all testers.
Fields: email (matches them to a Tester), optional free-text feedback. Submitting
creates a Run Queue row with `status = pending`. The tester re-submits this form
each time they want a run — "request = command", per the chosen model.

### 4.3 Results
On provisioning, the operator shares the tester's per-tester Notion page
(read-only, link-access) **once**. Results land in that page's Tracker. The
default Tracker view is filtered to Pursue/Consider so testers see signal, not the
dedup-memory rows. Run-complete notification is out of scope for the beta — testers
check their Tracker. (Optional per-tester webhook is a possible later add.)

## 5. Notion data model

### 5.1 Control plane — operator-owned, single set

**Testers DB** — one row per tester:
| Field | Purpose |
|-------|---------|
| `email` (title) | identity key |
| `name` | display |
| `status` | `pending_setup` / `active` / `setup_failed` / `paused` / `archived` |
| `signup_*` | raw signup fields (variant, country, work modes, CV, …) |
| `favorites_raw` | comma-separated company names from signup (≤3) |
| `tracker_db_id`, `profile_db_id`, `favorites_db_id`, `runs_db_id` | filled at provisioning |
| `page_id` | the per-tester parent page |
| `last_run_at` | rate-limit input |
| `runs_total`, `runs_7d` | rate-limit + observability |
| `cost_usd_total` | accumulated spend |
| `consent_at` | consent timestamp |

**Run Queue DB** — one row per run request:
| Field | Purpose |
|-------|---------|
| `email` | matches a Tester |
| `requested_at` | ordering |
| `status` | `pending` / `processing` / `done` / `skipped` / `failed` |
| `skip_reason` | e.g. `rate_limited`, `tester_not_found`, `tester_paused` |
| `fire_run_id` | which batch fire handled it |
| `feedback` | optional free-text from the tester |

### 5.2 Data plane — per tester, 4 DBs each

Each tester gets the **existing funded-drop layout** unchanged: `Tracker`,
`Profile`, `Favorites`, `Runs`, created under a per-tester page
(`<parent>/Testers/<email>`). Because the layout is identical to single-tenant,
the reused pipeline code reads/writes it with no schema changes — only the four
DB IDs vary per tester.

## 6. The batch fire

One scheduled Claude Code routine per day. Single container, processes all
eligible testers, then exits. Stages:

### Phase 0 — Control-plane read
Load operator config from env (`FD_NOTION_TOKEN`, `FD_PARENT_PAGE_ID`,
`FD_TESTERS_DB_ID`, `FD_RUN_QUEUE_DB_ID`). Read all Testers and all `pending`
Run Queue rows.

### Phase 1 — Provisioning (new testers)
For each Tester with `status = pending_setup`:
- Create the per-tester page + 4 DBs (reuse `setup/notion_init` headlessly — no
  interactive wizard).
- Write the Profile row from signup fields, applying beta defaults (§11):
  `window_days = 7`, favorites ≤ 3.
- Write the 4 DB IDs + `page_id` back to the Tester row; set `status = active`.
- On failure: `status = setup_failed`, log, continue. One tester never blocks others.

### Phase 2 — Eligibility
For each `pending` queue row, match to a Tester and apply deterministic checks
(plain Python `if` — pillar 2):
- Tester exists and `status = active` → else `skipped` with reason.
- `now - last_run_at ≥ min_hours_between_runs` → else `skipped: rate_limited`.
- `runs_7d < max_runs_per_week` → else `skipped: rate_limited`.
- At most **one** run per tester per fire (extra pending rows → `skipped`).
- Cap the eligible set at `testers_per_fire_max` (wall-clock guard, §14);
  overflow stays `pending` for the next fire.

### Phase 3 — Shared discovery (once)
Fetch the **12 VC sources once**, hold the raw job list in memory. The VC roster
is identical for every tester, so this is fetched a single time and reused — the
core optimization (see §7). Per-tester **Favorites** are fetched individually
(≤3 companies each, negligible).

### Phase 4 — Per-tester pipeline loop
For each eligible tester, sequentially:
1. Start from the shared raw VC snapshot + this tester's favorites.
2. Apply this tester's deterministic prefilter (their Profile).
3. Dedup against this tester's Tracker.
4. Branch on **cold start vs steady state** (§8).
5. Pass A (Haiku screener) → Pass B (Opus scorer) on the resulting set.
6. Write evaluated rows to the tester's Tracker; write a Runs row to their Runs DB.
7. Accumulate cost (§9); update the queue row → `done`; update the Tester row:
   `last_run_at`, `runs_total++`, `runs_7d`, `cost_usd_total +=`.
- Any exception inside one tester's pipeline → mark that queue row `failed`, write
  a minimal Runs row, continue to the next tester.

### Phase 5 — Operator summary
Write one operator-level summary (an **Operator Runs DB** row or a log): testers
processed, skipped, failed; total jobs scored; total cost; error roll-up.

## 7. Shared discovery optimization

The 12-VC discovery is **tenant-independent** — same roster, same ~4,000 jobs,
regardless of profile. Fetching once and sharing across all testers turns
discovery HTTP from N×4,000 into 1×4,000. This is in-fire memory caching only
(allowed by CLAUDE.md; cross-*fire* caching remains banned).

Implementation: `discovery` is refactored into two callables —
- `fetch_raw()` — pulls the 12 VCs, returns the raw job list. Called **once** per fire.
- `filter_for(profile, raw_jobs, tracker_urls)` — per-tester prefilter + dedup.

Favorites stay per-tester (`favorites.fetch(profile)`), called inside the loop.

## 8. Cold-start / seed mode

A tester's first fire has an empty Tracker → dedup removes nothing → the full
prefilter survivor set would hit Opus. The data shows this is ~700 jobs yielding
~0 useful matches. Mitigation, two levers together:

- **7-day window** (`window_days = 7`, a Profile setting) — roughly halves the
  cold-start candidate count and keeps the Tracker tidy. Reduces *rows*, not cost.
- **Top-N Opus cap** — on the first fire only, after Pass A, rank survivors and
  Opus-score only the top `cold_start_opus_cap` (~50). This is the **cost ceiling**.

First-fire flow:
1. Discovery + prefilter + (no dedup) + Pass A on all survivors (Haiku, cheap).
2. Rank Pass A survivors; Opus-score the **top ~50** → real Pursue/Consider/Skim
   rows, genuine value on day one.
3. Remaining survivors → written to the Tracker as lightweight **dedup-memory
   rows** (`status = carried`, unscored). They exist only so the next fire dedups
   instead of re-scoring. Hidden by the filtered default view.

Steady-state fires (Tracker non-empty): unchanged — Pass A on the new delta, Opus
on survivors (~15–25 jobs typically).

"Is this a cold start?" = the tester's Tracker has zero rows (or a `seeded` flag
on the Tester row).

## 9. Cost tracking

`cost_usd` is currently hardcoded to `0.0` (`orchestrator.py:605`) — there is no
ground truth today. This must be built; it is what makes the beta's economics
visible.

- New `cost/` module: a static price table (per model: input / output / cache
  $/Mtok) + an accumulator.
- Each `screener` (Haiku) and `scorer` (Opus) agent call reports token usage in
  its output JSON (`_usage: {input_tokens, output_tokens, cache_read, ...}`).
- The orchestrator's finalize stage sums usage, prices it, writes the real number
  to the tester's Runs row `cost_usd` and adds to the Tester row `cost_usd_total`.
- The operator summary (Phase 5) reports total fire cost.

Until two or three fires have run with this in place, all dollar figures are
estimates. Build this **first** — it de-risks every cap value below.

## 10. Pricing baseline (estimate — replace with measured numbers)

- Pass B Opus, full JD: ≈ $0.15–0.40 / job.
- Pass A Haiku: negligible (~$1–2 even at cold-start volume).
- Cold start, capped at ~50 Opus jobs: ≈ **$10–20 / tester, one-time**.
- Steady-state fire (~15–25 Opus jobs): ≈ **$3–8 / fire**.
- 15-tester beta: onboarding ≈ $200–300 total; ongoing ≈ $5/fire × requests.

## 11. Caps & beta config

A single config block (`config/beta.py`). Suggested starting values:

| Constant | Value | Rationale |
|----------|-------|-----------|
| `window_days` | 7 | Cold-start row reduction; flat for all fires |
| `cold_start_opus_cap` | 50 | First-fire cost ceiling |
| `favorites_max` | 3 | "A couple"; favorites skip Pass A → straight to Opus |
| `min_hours_between_runs` | 24 | ≥1 day between a tester's runs |
| `max_runs_per_week` | 2 | Rolling 7-day cap on ongoing cost |
| `testers_per_fire_max` | 12 | Wall-clock guard; overflow carries to next fire |
| `beta_tester_cap` | 20 | Total enrolled; bounds onboarding spend |

All are constants, not per-tester settings — uniform beta behavior.

## 12. Module map (new repo `funded-drop-beta`)

```
control/      NEW — Testers DB, Run Queue, provisioning, eligibility/rate-limit
cost/         NEW — token-usage capture, price table, accumulation
orchestrator  CHANGED — the batch fire (phases 0–5)
discovery/    CHANGED — fetch_raw() (shared) + filter_for() (per-tester)
evaluation/   CHANGED — screener/scorer; cold-start top-N cap added; usage reported
state/        REUSED — Notion I/O, now parameterized by per-tester DB IDs
notify/       REUSED — per-tester summary; operator summary added
setup/        REUSED — notion_init invoked headlessly by provisioning
config/       CHANGED — VC roster + beta.py config constants
```

Still ~one responsibility per module (CLAUDE.md pillar 6). `control/` and `cost/`
are the only genuinely new modules.

## 13. Reuse vs new

- **Reused as-is:** `state/*` (per-tester DB IDs already parameterizable),
  `evaluation` agents, `notify` summary, `setup/notion_init`, the VC roster.
- **Changed:** `discovery` split into shared-fetch + per-tester-filter;
  `orchestrator` becomes the multi-tenant batch loop; `evaluation` gains the
  cold-start cap and usage reporting.
- **New:** `control/` (multi-tenancy), `cost/` (real cost tracking).

## 14. Wall-clock & Notion volume

- **Container time limit.** N testers processed sequentially in one container;
  cold-start testers are slow. `testers_per_fire_max = 12` bounds it; overflow
  carries to the next day's queue. If still tight, the operator can schedule a
  second daily fire (15-run budget easily allows it).
- **Notion API pacing.** One token, ~3 req/s shared (existing client). Cold-start
  writes are the worst case: ~150 Tracker rows/tester × 0.34s ≈ 50s/tester. 12
  cold starts ≈ 10 min of writes. Acceptable at beta scale; revisit beyond ~50
  testers.
- **N×4 databases** clutter the operator's Notion. Fine for ≤20 testers; would
  need the shared-multi-tenant layout at larger scale.

## 15. Privacy & consent

Signup form has a required consent checkbox with this exact line:

> Your CV and preferences are stored in the operator's Notion and sent to
> Anthropic for job scoring. The operator can access this data.

Consent timestamp recorded on the Tester row (`consent_at`). True zero-access is
not achievable — the routine runs on the operator's token and sends CVs to
Anthropic — so the spec is honest about "operator has access, won't browse."

## 16. Future — untying from Claude Code

Today the runtime is a CC routine and the LLM calls go through `Agent(screener)` /
`Agent(scorer)`. To run without a CC subscription later, two layers swap; the rest
is unchanged:
- **LLM dispatch** — replace agent calls with direct Anthropic Messages API
  calls. Keep dispatch behind a thin `evaluation` interface so the swap is local.
- **Scheduler** — replace the CC routine schedule with cron / a small server.

The phase structure of the batch fire, the Notion data model, and the cost module
all carry over unchanged. Designing the LLM dispatch behind one interface now is
the only forward-looking concession this spec makes.

## 17. Open risks

1. **Cost tracking depends on agents reporting usage** — if the harness doesn't
   surface token counts to agent output, fall back to char-count estimation.
2. **Wall-clock** under many simultaneous cold starts — mitigated by
   `testers_per_fire_max` + optional second daily fire.
3. **Onboarding stampede** — 15 routine runs/day; if >~12 testers sign up the same
   day, provisioning + first fires spill across days. Stagger invitations.
4. **No tester notification** — beta testers must check their Tracker manually.
   Accepted; webhook/email is a later add.

## 18. Build phases

1. **Cost tracking** in single-tenant `funded-jobs-drop` first — small, gives
   ground truth, de-risks every cap. (Can land before the repo fork.)
2. **Fork** `funded-drop-beta`; add `config/beta.py`.
3. **`control/`** — Testers DB + Run Queue schemas, Notion forms, provisioning,
   eligibility/rate-limit.
4. **Shared discovery** refactor (`fetch_raw` / `filter_for`).
5. **Cold-start cap** in `evaluation` + first-fire branch in the orchestrator.
6. **Batch orchestrator** — wire phases 0–5 together.
7. **Operator summary** + dry-run over 2–3 seed testers; read real cost; tune caps.
