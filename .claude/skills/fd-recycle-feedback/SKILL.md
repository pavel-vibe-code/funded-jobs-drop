---
name: fd-recycle-feedback
description: Read user feedback from Tracker (Match quality changes + Feedback text), synthesize updated learned_exclusions + learned_examples via the qa agent, write back to Profile, and archive rows the user explicitly rejected. Idempotent — re-running on the same feedback yields the same rules. Also runs automatically as Step 1 of every /fd-run.
---

# /fd-recycle-feedback — Funded Drop learning loop

You orchestrate one cycle of feedback recycling. Same hard rules as `/fd-run`:

- **Unattended execution.** No permission prompts, no user-input pauses.
- **Shell state does NOT persist between Bash calls.** Capture run_id once from Step 0; substitute the literal value wherever `<RUN_ID>` appears below.
- **Bounded retry.** If the qa agent fails or writes malformed JSON, retry **once** and continue. Don't loop.
- **Errors don't crash the cycle.** A missing `qa-output.json` at apply time is logged and the cycle ends gracefully — Profile stays unchanged.

## Step 0 — Generate run_id

```bash
python3 -c "import uuid; print(uuid.uuid4().hex)"
```

Read the output and substitute the literal value for `<RUN_ID>` below.

## Step 1 — Prepare (Python)

```bash
python3 -m recycle_feedback prepare <RUN_ID>
```

Reads Tracker rows where `Match quality != OK` OR `Feedback` has text, and reads the current `learned_exclusions` + `learned_examples` from Profile. Writes `/tmp/fd-recycle/<RUN_ID>/feedback-input.json`.

If the printed feedback count is 0, **stop here** — nothing to learn from. Report "no feedback to recycle" and exit.

## Step 2 — qa agent (single dispatch)

Dispatch the `qa` agent:

> Read `/tmp/fd-recycle/<RUN_ID>/feedback-input.json` and produce refined learned rules per your spec. Write the raw JSON object (no preamble, no markdown fences) to `/tmp/fd-recycle/<RUN_ID>/qa-output.json`. Don't echo anything else.

If the agent's reply suggests failure (no confirmation, error message), re-dispatch the **same** prompt once. Then proceed regardless.

## Step 3 — Apply (Python)

```bash
python3 -m recycle_feedback apply <RUN_ID>
```

Does two things:

1. **Archives user-rejected rows.** Any feedback row where Match quality = `Feedback` AND the Feedback text is user-typed (not the scorer's `[Auto]`-prefixed note) gets Status set to `Dropped (feedback)` — out of the active view, and out of future feedback pools. This is deterministic and runs even if the qa agent failed.
2. **Writes refined rules.** Reads `qa-output.json` and writes the new `learned_exclusions` + `learned_examples` to the Profile row. If the agent's output is missing or malformed, this part logs the issue and exits cleanly without touching Profile (the archiving in step 1 still happened).

## Step 4 — Report

Print the rationale (from `qa-output.json`) so the user knows what changed:

```bash
python3 -c "import json; d = json.load(open('/tmp/fd-recycle/<RUN_ID>/qa-output.json')); print(d.get('rationale', '(no rationale)'))" 2>/dev/null || echo "(qa-output missing)"
```

## Routine permissions (informational)

If this skill is wired into a scheduled routine, the allowlist must include:

- `Bash(python3 -m recycle_feedback *)`
- `Bash(python3 -c *)`
- `Agent(qa)`
- `Read(/tmp/fd-recycle/**)`, `Write(/tmp/fd-recycle/**)`

## Dry-run mode

If `FD_DRY_RUN=1` is set, `prepare` returns an empty feedback list (no Notion read), and `apply` is a no-op (no Notion write).
