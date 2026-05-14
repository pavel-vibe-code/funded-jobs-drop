---
name: fd-run
description: Run a single fire — fetch fresh jobs from 7 Consider VCs + 5 Getro VCs + active Favorites, dedup against Tracker, apply deterministic filters, run Evaluation (stubbed in v0.1.0 Phase 2; real LLM scoring in Phase 3), write to Tracker, print summary.
---

# /fd-run

You are triggering one fire of the Funded Drop pipeline.

## Process

Invoke the orchestrator:

```bash
python3 -m orchestrator
```

This runs the full pipeline:
1. Read Profile from Notion (`state.profile.read`)
2. Read Tracker's known-URL index (active rows only, filtered server-side)
3. **Discovery**: fetch from all 7 Consider VCs + 5 Getro VCs + active Favorites
4. Cross-source dedup on canonical URL
5. Drop URLs already in Tracker (state-side dedup)
6. **Prefilter S2-S9**: work mode, country/relocation, seniority, company blacklist, industry exclusion, salary floor
7. **Evaluation** (Phase 2 stub: every survivor marked Strong; Phase 3 will add real LLM scoring)
8. Write new rows to Tracker
9. Print fire summary

## Common errors

- **Auth error** → token missing/invalid; check `~/.claude/settings.local.json`
- **SetupError** with "missing column" → schema drift; run `/fd-setup --repair`
- **SetupError** with "data sources" → user accidentally added a stray data source in Notion UI; delete it, then `/fd-setup --repair`
- **NotionError HTTP 4xx** → fix the underlying issue per the error body
- **Rate limit** → client retries automatically with exponential backoff; if persistently failing, wait and retry

## Phase 2 limitations (current alpha)

- Evaluation is stubbed — every survivor is marked Strong (Pursue)
- No real LLM cost
- No webhook push (would-be Pursue rows just go to Tracker)
- Runs DB not yet written; only console summary
- Closure detection not yet automated

These ship in Phase 3 (LLM scoring) and Phase 4 (learning loop + closure detection).
