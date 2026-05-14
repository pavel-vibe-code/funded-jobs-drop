---
name: fd-run
description: Run a single fire — fetch fresh jobs from 7 Consider VCs + 5 Getro VCs + active Favorites, dedup against Tracker, apply deterministic filters, screen with Pass A (Haiku), score survivors with Pass B (Opus), write to Tracker, write Runs row, push Pursue rows to webhook.
---

# /fd-run — Funded Drop pipeline orchestrator

You are the orchestrator. The fire is a sequence of **deterministic Python stages** (invoked via `python3 -m orchestrator <stage> <run_id>`) interleaved with **parallel LLM agent dispatches** (screener, scorer, summarize).

Python does the deterministic work; agents do the LLM judgment; you wire them together. Per-fire state lives in `/tmp/fd-run/<run_id>/`. Notion holds cross-fire state.

## Hard rules

- **Unattended execution.** This skill runs in Cloud Routine — no permission prompts, no user-input pauses. Every decision (when to retry, when to skip ahead, when to give up) is yours to make autonomously. Never stop and ask.
- **Shell state does not persist between Bash calls.** Each Bash tool call starts a fresh shell. **Do NOT use shell variables like `$RUN_ID` across calls.** Capture run_id from Step 0 as a literal string and substitute it inline into every subsequent command.
- **Bound retries.** If an agent dispatch produces malformed/missing output, retry **once** and continue. Don't loop indefinitely — wall time is a routine budget constraint.
- **Errors don't block the fire.** A failing stage gets logged and the pipeline continues to finalize. Empty/missing inputs at any stage → skip downstream stages, go straight to finalize. The Runs row records what happened.

## Step 0 — Generate run_id

```bash
python3 -c "import uuid; print(uuid.uuid4().hex)"
```

Read the output (32 hex chars). **From here on, substitute that literal value wherever `<RUN_ID>` appears below.** Do not use `$RUN_ID` — shell vars don't persist between Bash calls.

## Step 1 — Discovery (Python)

```bash
python3 -m orchestrator discovery <RUN_ID>
```

Fetches candidates from all sources, dedups, applies deterministic prefilter S2–S9, batches survivors into `/tmp/fd-run/<RUN_ID>/candidates-batch-{0..N-1}.json`.

Count batches:

```bash
ls /tmp/fd-run/<RUN_ID>/candidates-batch-*.json 2>/dev/null | wc -l
```

- Result `0` → no candidates this fire. Skip directly to **Step 6 (finalize)**.
- Result `≥1` → continue to Step 2.

## Step 2 — Pass A: Screener (parallel agent dispatch)

For each `candidates-batch-{N}.json`, dispatch the `screener` agent. Cap parallel dispatches at **8 per message** (WAVE_SIZE). If there are more than 8 batches, send sequential messages of up to 8 dispatches each — but each individual message dispatches all its agents in parallel.

**Prompt template** (substitute `<RUN_ID>` and `<N>` with literal values):

> Read `/tmp/fd-run/<RUN_ID>/candidates-batch-<N>.json` and screen every candidate against the profile in that file. Per your spec, return one verdict object per candidate. Write the raw JSON array (no preamble, no markdown fences) to `/tmp/fd-run/<RUN_ID>/screener-verdicts-<N>.json`. Don't echo anything else.

After all waves return, verify count:

```bash
ls /tmp/fd-run/<RUN_ID>/screener-verdicts-*.json 2>/dev/null | wc -l
```

If less than the batch count, re-dispatch just the missing batches **once**. Then proceed regardless of whether the retry completed all — `aggregate` will work on whatever verdict files exist; missing ones are treated as "drop" (candidate not in survivors).

Then aggregate:

```bash
python3 -m orchestrator aggregate <RUN_ID>
```

## Step 3 — JD fetch (Python)

```bash
python3 -m orchestrator jd_fetch <RUN_ID>
```

Source-aware fetch — Getro detail page first, ATS adapter fallback for Consider + Favorites. Produces `scorer-input-{idx}.json` per survivor + `jd-failed.json` for failures.

Count scorer inputs:

```bash
ls /tmp/fd-run/<RUN_ID>/scorer-input-*.json 2>/dev/null | wc -l
```

- Result `0` → no JDs fetched. Skip to **Step 4b (write)** — the failures still get written to Tracker as `jd_fetch_failed`.
- Result `≥1` → continue to Step 4.

## Step 4 — Pass B: Scorer (parallel agent dispatch)

For each `scorer-input-{idx}.json`, dispatch the `scorer` agent. WAVE_SIZE=8.

**Prompt template** (substitute `<RUN_ID>` and `<IDX>` with literal values):

> Read `/tmp/fd-run/<RUN_ID>/scorer-input-<IDX>.json` and produce a verdict per your spec. Write the raw JSON object (no preamble, no markdown fences) to `/tmp/fd-run/<RUN_ID>/scorer-output-<IDX>.json`. Don't echo anything else.

After all waves return, verify count:

```bash
ls /tmp/fd-run/<RUN_ID>/scorer-output-*.json 2>/dev/null | wc -l
```

Re-dispatch missing ones once, then proceed regardless.

## Step 4b — Tracker write (Python)

```bash
python3 -m orchestrator write <RUN_ID>
```

Reads scorer outputs + jd-failed, writes rows to Notion Tracker, builds `summarize-input.json`.

## Step 5 — Summary (single agent dispatch)

Dispatch the `summarize` agent once:

> Read `/tmp/fd-run/<RUN_ID>/summarize-input.json` and produce the per-fire summary per your spec. Write the raw JSON object (no preamble, no markdown fences) to `/tmp/fd-run/<RUN_ID>/summary.json`. Don't echo anything else.

If the agent fails or `summary.json` is missing, the finalize stage falls back to an auto-generated summary. Don't retry.

## Step 6 — Finalize (Python)

```bash
python3 -m orchestrator finalize <RUN_ID>
```

Writes the Runs row to Notion. POSTs the webhook if the user has Pursue rows AND webhook is configured/enabled. Webhook errors are non-fatal and logged in the Runs row.

## Step 7 — Report

Display `/tmp/fd-run/<RUN_ID>/finalize-result.json`. In manual mode the user reads it; in routine mode it lands in the run log.

```bash
cat /tmp/fd-run/<RUN_ID>/finalize-result.json
```

## Routine permissions (informational)

The routine plugin must auto-allow these tool patterns for the fire to run unattended:

- `Bash(python3 -m orchestrator *)`
- `Bash(python3 -c *)`
- `Bash(ls /tmp/fd-run/*)`
- `Bash(cat /tmp/fd-run/*)`
- `Agent(screener)`, `Agent(scorer)`, `Agent(summarize)`
- `Read(/tmp/fd-run/**)`, `Write(/tmp/fd-run/**)`

If any of these prompt for permission at fire time, the routine setup is incomplete.

## Dry-run mode

If `FD_DRY_RUN=1` is set in the environment, all Notion writes and webhook POSTs are no-ops. Useful for local testing.
