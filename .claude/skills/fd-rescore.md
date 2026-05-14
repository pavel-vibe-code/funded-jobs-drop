---
name: fd-rescore
description: Re-evaluate (Pass B only) existing Tracker rows in-place against the current Profile. Three modes — `failed` (retry jd_fetch_failed rows after adapter fixes), `stale` (rows whose profile_hash drifted, e.g. after /fd-settings edits), `flagged` (rows where Match quality is Wrong fit / Great match — closes the /fd-recycle-feedback loop).
---

# /fd-rescore — Pass B re-evaluation

You orchestrate one rescore pass over existing Tracker rows. Same hard rules as `/fd-run`:

- **Unattended execution.** No prompts, no pauses. Decide retries autonomously, bounded to 1.
- **Shell state does NOT persist between Bash calls.** Capture run_id once; substitute `<RUN_ID>` literal everywhere.
- **Errors don't kill the run.** Per-row update failures get logged; the rest continues.

Modes and behaviour:

| mode | selects | webhook on new Pursue? |
|---|---|---|
| `failed` | `Status == jd_fetch_failed` rows | **yes** (these never got a real verdict before) |
| `stale` | rows where `profile_hash_at_eval != current profile.profile_hash` | no |
| `flagged` | rows where `Match quality ∈ {Wrong fit, Great match}` | no |

The user picks the mode. If they say "rescore the failed ones" → `failed`. "I changed my profile" → `stale`. "Re-check the ones I flagged" → `flagged`. If unclear, ask **one** short question and pick the mode they want.

## Step 0 — Generate run_id

```bash
python3 -c "import uuid; print(uuid.uuid4().hex)"
```

Capture the 32-hex string. Substitute its literal value for `<RUN_ID>` everywhere below. Pick `<MODE>` per the table above.

## Step 1 — Select & fetch JDs (Python)

```bash
python3 -m orchestrator rescore_select <RUN_ID> <MODE>
```

Reads matching Tracker rows, fetches fresh JDs for each, writes:
- `/tmp/fd-run/<RUN_ID>/scorer-input-<idx>.json` per fetched JD
- `/tmp/fd-run/<RUN_ID>/rescore-jd-failed.json` for unrecoverable rows
- `/tmp/fd-run/<RUN_ID>/rescore-selected.json` + `rescore-mode.json` (metadata)

Count scorer inputs:

```bash
ls /tmp/fd-run/<RUN_ID>/scorer-input-*.json 2>/dev/null | wc -l
```

- `0` → no JDs fetched. Skip to **Step 3 (apply)** so the still-failing rows get their status refreshed, then finalize.
- `≥1` → continue.

## Step 2 — Scorer dispatch (parallel agents)

For each `scorer-input-<idx>.json`, dispatch the `scorer` agent. Cap at **8 per message**.

**Prompt template** (substitute `<RUN_ID>` and `<IDX>` literally):

> Read `/tmp/fd-run/<RUN_ID>/scorer-input-<IDX>.json` and produce a verdict per your spec. Write the raw JSON object (no preamble, no markdown fences) to `/tmp/fd-run/<RUN_ID>/scorer-output-<IDX>.json`. Don't echo anything else.

After all waves return, verify count:

```bash
ls /tmp/fd-run/<RUN_ID>/scorer-output-*.json 2>/dev/null | wc -l
```

Re-dispatch missing ones once. Then proceed regardless.

## Step 3 — Apply (Python)

```bash
python3 -m orchestrator rescore_apply <RUN_ID> <MODE>
```

Updates each Tracker row in-place: Match, Why fits, Status, pass_b_*, blocker/indicator detections, `profile_hash_at_eval`. Does **not** touch user-owned columns (Match quality, Feedback). Writes `webhook-verdicts.json` (populated only in `failed` mode for newly-Pursue rows).

## Step 4 — Summary (agent dispatch)

Dispatch the `summarize` agent once:

> Read `/tmp/fd-run/<RUN_ID>/summarize-input.json` and produce the summary per your spec. Write the raw JSON object (no preamble, no markdown fences) to `/tmp/fd-run/<RUN_ID>/summary.json`. Don't echo anything else.

If the agent fails, finalize falls back to an auto-generated summary.

## Step 5 — Finalize (Python)

```bash
python3 -m orchestrator finalize <RUN_ID>
```

Creates the Runs row. Webhook fires only if `webhook-verdicts.json` has Strong rows (so `stale` / `flagged` modes are silent even if scores upgrade).

## Step 6 — Report

```bash
cat /tmp/fd-run/<RUN_ID>/finalize-result.json
```

## Routine permissions (informational)

Same allowlist as `/fd-run` plus:

- `Bash(python3 -m orchestrator rescore_select *)`
- `Bash(python3 -m orchestrator rescore_apply *)`

## Dry-run mode

If `FD_DRY_RUN=1` is set, `rescore_select` returns no rows (no Notion read), nothing to score, `rescore_apply` is a no-op.
