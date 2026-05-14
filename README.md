# Funded Drop

Automated job search across VC-backed companies. Pulls postings weekly from 12 top VCs (Consider + Getro platforms), filters deterministically against your profile, scores with two-tier LLM evaluation, writes results to Notion and pushes new matches to your configured webhook (Slack / Discord / Teams / Zapier / anywhere).

**Status**: v0.1.0-alpha — under active development, not yet released.

## Coverage

- **7 Consider VCs**: a16z, Sequoia, Greylock, Lightspeed, Bessemer, Kleiner Perkins, Balderton
- **5 Getro VCs**: Accel, General Catalyst, Atomico, Index, Insight Partners
- **~2,300 unique portfolio companies** in scope
- **AI-50 supplement**: 14 high-profile AI companies our VCs don't cover (opt-in toggle)
- **User favorites**: pin individual companies via direct ATS adapters

## How it works

1. **Discovery** (deterministic): fetch fresh jobs from configured sources, dedup, apply your filters (region, work mode, seniority, salary floor, exclusions)
2. **Evaluation** (LLM, two-pass): cheap Pass A screener triages on structured tags, expensive Pass B scorer reads the full JD and assigns tier (Strong / Decent / Stretch)
3. **State** (Notion): writes to your Tracker DB; tracker is the single source of truth
4. **Notify**: composes a per-fire summary in Runs DB, posts to your webhook on new Strong matches
5. **QA loop**: your Match-quality feedback refines LLM instructions over time

## Setup

See `INSTALL.md` (coming in v0.1.0 release).

## Design principles

See [`CLAUDE.md`](./CLAUDE.md).
