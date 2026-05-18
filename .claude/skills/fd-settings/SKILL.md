---
name: fd-settings
description: Conversational editor for the user's Profile. Shows current values, lets the user change any subset (search prefs, salary floor, webhook config, etc.), persists to the Profile Notion DB. Also lets the user view qa-learned rules.
---

# /fd-settings — edit Profile settings

Conversational, not menu-driven. You read the current Profile, present it grouped into sections, ask what to change, then apply the changes.

## Step 1 — Read current Profile

```bash
python3 -c "from state.profile import read; import json; p = read(); print(json.dumps({k: v for k, v in p.__dict__.items() if k not in ('page_id', 'profile_hash')}, indent=2, default=str))"
```

Display the result to the user, grouped into the three sections below. Use plain text, not JSON.

### Section A — Search preferences

| Field | Description |
|---|---|
| `variant` | `EU` or `US` — controls VC roster + region filters |
| `eu_include_uk_ie` | (EU only) include UK/Ireland-tagged jobs |
| `home_country`, `home_state`, `home_city` | for residency filtering at scorer stage |
| `work_modes` | subset of `["Remote", "Hybrid", "On-site"]` |
| `search_outside_home` | if True, allow remote-from-elsewhere postings |
| `willing_to_relocate` | if True, country-mismatch residency requirements don't penalize |
| `accepted_seniority` | subset of `["junior", "mid", "senior", "staff", "principal"]` |
| `salary_floor_amount` + `salary_floor_currency` | annual floor (USD/EUR/GBP); jobs disclosing below this are dropped at S9 prefilter |
| `interest_description` | free text — what kind of role excites you |
| `pursue_blockers` | free text — disqualifying patterns (e.g. "defense, gambling") |
| `stretch_indicators` | free text — partial-fit signals (e.g. "early-stage, equity-heavy") |
| `cv_url`, `cv_summary` | self-explanatory; cv_summary is what scorer sees |
| `excluded_companies`, `excluded_industries` | multi-select blacklists |

### Section B — System settings

| Field | Description |
|---|---|
| `posted_since_window` | `1 week` / `2 weeks` / `1 month` |
| `ai50_seed_enabled` | enable the 14-company AI-50 supplement (Cohere, Cognition, etc.) |
| `webhook_url`, `webhook_enabled`, `webhook_notify_tier` | webhook destination + on/off, and which match tier triggers a push — `Strong — Pursue` (default) or `Decent — Consider` (also notify on Consider) |

### Section C — qa-learned rules (read-only)

| Field | Description |
|---|---|
| `learned_exclusions` | qa-synthesized exclusion rules from your feedback |
| `learned_examples` | qa-synthesized example matches/mismatches |

These two are populated by `/fd-recycle-feedback`. If you want to clear or rewrite them manually, edit them like any other field below.

## Step 2 — Ask what to change

After showing current values, ask the user **one** open question: "Which field(s) do you want to change?" Don't list options exhaustively — they can read the table.

If they want to change something not in the Profile (e.g., reset everything, add a Favorite, change the Notion parent page), explain that this skill only edits Profile fields. For Favorites: tell them to add rows directly in the Favorites Notion DB. For re-setup: `/fd-setup --repair`.

## Step 3 — Apply changes

For each field the user wants to change, collect the new value via conversation. For multi-select fields (`work_modes`, `accepted_seniority`, `excluded_companies`, `excluded_industries`), confirm the full list they want, not a delta.

Apply all changes in **one** update call:

```bash
python3 -c "
from state.profile import update
update(
    field_name=value,
    another_field=value,
)
"
```

Substitute the actual field names + Python-literal values. For lists, use Python list literal: `work_modes=['Remote', 'Hybrid']`.

`state.profile.update` recomputes `profile_hash` automatically. You don't need to set it.

## Step 4 — Confirm

Display the fields that changed (`old → new`). Mention that `profile_hash` was recomputed — meaning the next `/fd-run` will re-score any Tracker rows whose `profile_hash_at_eval` no longer matches (rescore behavior ships in a later phase; for now, only new candidates feel the change).

## Notes for routine compatibility

This skill is **manual-only**. It's conversational, so it requires a human in the loop. Don't wire it into a Cloud Routine — there's no autonomous path here. (`/fd-run` and `/fd-recycle-feedback` are the routine-compatible commands.)
