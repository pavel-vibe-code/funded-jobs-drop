---
name: fd-info
description: List all Funded Drop slash commands with a one-line description of each. Reference card for the user.
---

# Funded Drop — command reference

Display the table below to the user verbatim. Don't paraphrase, don't add commentary — they need a quick reference card.

| Command | What it does |
|---|---|
| `/fd-setup` | First-time setup wizard. Creates the four Notion databases (Tracker, Profile, Favorites, Runs), populates Profile from your answers, optionally enables the AI-50 seed roster. Re-run with `--repair` to add missing columns. |
| `/fd-run` | Run one fire of the pipeline. Fetches new jobs from 7 Consider + 5 Getro VCs (+ active Favorites), dedups against Tracker, applies S2–S9 prefilter, screens with Pass A (Haiku), scores survivors with Pass B (Opus), writes to Tracker, posts Pursue rows to your configured webhook. Marks stale Tracker rows as Closed when their VC source confirms the job's gone. |
| `/fd-settings` | Edit your Profile (search preferences, salary floor, interest description, etc.), view system settings, view qa-learned rules. Conversational — pick a section, change what you want. |
| `/fd-recycle-feedback` | Run the learning loop. Reads Tracker rows where you changed `Match quality` from "OK" or added text in `Feedback`, sends them to the `qa` agent which synthesizes refined `learned_exclusions` + `learned_examples`, writes them back to your Profile so future fires apply your patterns. |
| `/fd-test-webhook` | Send a test message to your configured webhook URL. Confirms Slack/Discord/Teams/Zapier wiring before you depend on `/fd-run` pushing matches there. |
| `/fd-info` | This card. |

## Cloud Routine

`/fd-run` and `/fd-recycle-feedback` are designed to run unattended in Claude Code's Cloud Routine mode (scheduled fires, no human in the loop). The other commands are manual.

## Where things live

- Your jobs: the **Tracker** Notion DB (primary surface).
- Your settings: the **Profile** Notion DB (1 row).
- Pinned companies: the **Favorites** Notion DB.
- Per-fire history: the **Runs** Notion DB.

All four sit under the parent page you named at `/fd-setup` time.
