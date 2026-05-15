---
name: fd-setup
description: First-time setup wizard for Funded Drop. Walks the user through collecting their profile (variant, location, work modes, scoring criteria, CV, webhook), creates 4 Notion databases under their chosen parent page, writes the Profile row, and optionally seeds the 14 AI-50 supplement companies. Use --repair to validate/patch an existing workspace.
---

# /fd-setup

You are guiding the user through first-time setup of Funded Drop. Your job: collect 14 fields of answers, create the Notion workspace, write the Profile, and report success.

## Welcome message

Start with this (substitute the actual version from `VERSION` file):

> Welcome to **Funded Drop v0.1.0-alpha**
>
> Automated job search across VC-backed companies — 12 top VCs (~2,300 portfolio companies), deterministic filters + LLM scoring, results to Notion + optional webhook push.
>
> Setup takes ~5 minutes. You can edit any answer later via `/fd-settings`.

## Modes

| Invocation | Action |
|---|---|
| `/fd-setup` | Full wizard + creates 4 DBs + writes Profile. **Refuses** if a workspace is already configured — directs user to `--repair` / `--rewipe` / `/fd-settings`. |
| `/fd-setup --repair` | Skip wizard. Run `execute_repair()`: validate existing DBs, patch missing schema columns. Preserves all data. |
| `/fd-setup --rewipe` | Run the full wizard again, but only after the existing "Funded Drop" parent page has been archived in Notion (Move to Trash). Refuses otherwise so duplicate DBs can't be created. |

If the user types `/fd-setup --repair`: run `setup.runner.execute_repair()` directly, report which DBs got patched. Done.

If the user types `/fd-setup --rewipe`: confirm with them once that they've archived the existing parent page in Notion (or are about to), then walk the wizard, then call `execute_fresh(answers, rewipe=True)`. The runner re-checks Notion that the page is actually archived — if not, it'll refuse with instructions.

## Fresh setup flow

### Pre-flight: Notion access

1. Ask: "Have you created an internal Notion integration with read+write permissions? If not, walk you through it?"
   - If no: explain — go to notion.so/profile/integrations → New integration → Internal → grant Read/Write/Insert content permissions → copy the integration token.
2. Ask: "Paste your Notion integration token (starts with `secret_` or `ntn_`)."
3. Ask: "Paste the URL of the Notion page where Funded Drop should create its 4 databases. Make sure you've shared this page with your Funded Drop integration."
   - Parse `parent_page_id` from the URL (last 32 hex chars).

### Section 1: Region & location (5 fields)

Ask each in sequence — use `AskUserQuestion` where options are constrained:

- `variant` — "EU or US?" (select: EU / US)
- If `variant == EU`: `eu_include_uk_ie` — "Include jobs in UK and Ireland?" (checkbox)
- `home_country` — "What country do you live in?" (free text — e.g., Czechia, France, Germany, USA)
- If `variant == US`: `home_state` — "Which US state?" (free text — e.g., California, NY)
- `home_city` — "Which city?" (free text)

### Section 2: Work mode & relocation (3 fields)

- `work_modes` — "Which work modes work for you? (Multi-select)" Use AskUserQuestion with multiSelect=true. Options:
  - Remote
  - Hybrid
  - Onsite (includes Hybrid) — note: selecting this auto-accepts hybrid too
- `search_outside_home` — "Look for jobs outside your home country too?" (checkbox)
- If yes: `willing_to_relocate` — "Willing to relocate for hybrid/onsite roles abroad?" (checkbox)
  - If no: explain — "Then only remote roles outside your country will be surfaced, and only ones whose JD doesn't require local residency. The LLM will verify residency at scoring time."

### Section 3: Seniority & salary (3 fields)

- `accepted_seniority` — "Which seniority levels?" (multi-select: entry / mid / senior / staff / principal / executive)
- `salary_floor_amount` — "Salary floor (yearly)? Enter a number, or skip to disable this filter." (number, optional)
- `salary_floor_currency` — "Currency?" (select: USD / EUR / GBP / CHF / CAD / AUD / PLN / CZK / SEK)

### Section 4: Scoring criteria — the most important section (3 free-text fields)

Frame this carefully:

> "These three fields shape how the LLM evaluates jobs. They iterate over time as you flag matches via Feedback in the Tracker. Spend a minute on each."

- `interest_description` — "What kinds of roles do you want?" Long free text.
  > *Example: "Senior or Staff PM at AI-native B2B SaaS — infra, agentic, fintech, ~6+ yrs exp, IC track."*

- `pursue_blockers` — "What disqualifies a job from being rated Strong (Pursue)?" Long free text.
  > *Example: "Mandatory evening hours or on-call rotation. US citizenship required. Role primarily people management vs IC product. 5-day in-office mandate."*

- `stretch_indicators` — "What pushes a job from Decent (Consider) toward Stretch (Skim)?" Long free text.
  > *Example: "'Wear many hats' phrasing (vague role). 'Rapidly growing team' (often burnout). >10 yrs exp required (probably too senior). Role description leans ops more than product."*

### Section 5: CV (2 fields, optional but recommended)

- `cv_url` — "Link to your CV (Drive/Notion/etc.) — optional."
- `cv_summary` — "Paste a 2–3 paragraph summary of your background. The LLM uses this for fit scoring." (long free text)

### Section 6: System settings (3 fields)

- `posted_since_window` — "How far back to look for new jobs each fire?" (select: 1 week / 2 weeks / 1 month, default 2 weeks)
- `ai50_seed_enabled` — "Enable the AI-50 supplement? Adds 14 high-profile AI companies our standard VCs don't cover (Cohere, Runway, SSI, etc.)." (checkbox, default off)
- `webhook_url` — "Webhook URL for notifications (Slack/Discord/Teams/Zapier — optional). Press enter to skip."
- If webhook_url provided: `webhook_enabled` — implicitly true; also offer to send a test message after setup.

## Executing setup

Build a `WizardAnswers` dict from the collected fields and run:

```bash
python3 -c "
from setup.runner import execute_fresh
from setup.wizard import WizardAnswers
answers = WizardAnswers(
    notion_token='...',
    parent_page_id='...',
    variant='...',
    # ... all fields
)
result = execute_fresh(answers, rewipe=<True if --rewipe was passed else False>)
print('Created DBs:', result.db_ids)
print('Profile page:', result.profile_page_id)
if result.ai50_seed_result:
    print('AI-50 seed:', result.ai50_seed_result)
"
```

If validation fails (returns `ValueError` with errors), surface the errors clearly and ask the user to fix.

If Notion errors occur:
- `AuthError` → token problem
- `SetupError` → parent page not shared with integration, or data_source count mismatch
- `NotionError` → other API issue; show the HTTP details

## Success report

After successful setup, show:

```
Setup complete. Created:
  Tracker DB:   <id>
  Profile DB:   <id>  (1 row written, profile_hash: <8-char prefix>)
  Favorites DB: <id>  (+14 AI-50 seed rows, if enabled)
  Runs DB:      <id>

Workspace config saved to ~/.claude/settings.local.json under 'funded-drop'.

Next steps:
- Run /fd-run for your first fire (won't pull yet — features ship in later phases)
- Edit settings via /fd-settings
- Browse your workspace at: https://notion.so/<parent_page_id>
```

## What setup does NOT do

- Doesn't run a fire (user does that via `/fd-run`)
- Doesn't install cron / scheduled triggers (cloud-routine config is separate)
- Doesn't validate the webhook destination automatically — offer `/fd-test-webhook` separately if user wants to verify
