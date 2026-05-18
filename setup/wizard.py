"""Wizard answer schema + validation + conversion to Profile.

The interactive Q&A flow lives in .claude/skills/fd-setup.md (where Claude
asks questions and collects answers). This module defines:
  - WizardAnswers dataclass: what gets collected
  - Option lists: the canonical values for each select field
  - validate(): catch malformed answers before hitting Notion
  - to_profile(): convert validated answers to a Profile dataclass
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from state.profile import Profile


# ─── Canonical option lists (must match notion_init.py schemas) ────────

VARIANT_OPTIONS = ["EU", "US"]
WORK_MODE_OPTIONS = ["Remote", "Hybrid", "Onsite (includes Hybrid)"]
SENIORITY_OPTIONS = ["entry", "mid", "senior", "staff", "principal", "executive"]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "CHF", "CAD", "AUD", "PLN", "CZK", "SEK"]
WINDOW_OPTIONS = ["1 week", "2 weeks", "1 month"]
NOTIFY_TIER_OPTIONS = ["Strong — Pursue", "Decent — Consider"]


@dataclass
class WizardAnswers:
    # Notion setup
    notion_token: str = ""
    parent_page_id: str = ""

    # Region & location
    variant: str = "EU"
    eu_include_uk_ie: bool = False
    home_country: str = ""
    home_state: str = ""        # only for US variant
    home_city: str = ""

    # Work mode & relocation
    work_modes: list[str] = field(default_factory=list)
    search_outside_home: bool = False
    willing_to_relocate: bool = False

    # Seniority & salary
    accepted_seniority: list[str] = field(default_factory=list)
    salary_floor_amount: Optional[float] = None
    salary_floor_currency: str = "USD"

    # Scoring criteria (free text)
    interest_description: str = ""
    pursue_blockers: str = ""
    stretch_indicators: str = ""

    # CV
    cv_url: str = ""
    cv_summary: str = ""

    # Settings
    posted_since_window: str = "2 weeks"
    ai50_seed_enabled: bool = False
    webhook_url: str = ""
    webhook_enabled: bool = False
    webhook_notify_tier: str = "Strong — Pursue"


def validate(answers: WizardAnswers) -> list[str]:
    """Return list of validation errors. Empty list means OK."""
    errors: list[str] = []

    if not answers.notion_token:
        errors.append("Notion token required.")
    if not answers.parent_page_id:
        errors.append("Notion parent page URL/ID required.")

    if answers.variant not in VARIANT_OPTIONS:
        errors.append(f"variant must be one of {VARIANT_OPTIONS}, got '{answers.variant}'.")
    if not answers.home_country:
        errors.append("home_country required.")
    if answers.variant == "US" and not answers.home_state:
        errors.append("home_state required for US variant.")
    if not answers.home_city:
        errors.append("home_city required.")

    if not answers.work_modes:
        errors.append("At least one work mode required.")
    for wm in answers.work_modes:
        if wm not in WORK_MODE_OPTIONS:
            errors.append(f"work_mode '{wm}' not in {WORK_MODE_OPTIONS}.")

    if not answers.accepted_seniority:
        errors.append("At least one seniority level required.")
    for sl in answers.accepted_seniority:
        if sl not in SENIORITY_OPTIONS:
            errors.append(f"seniority '{sl}' not in {SENIORITY_OPTIONS}.")

    if answers.salary_floor_amount is not None and answers.salary_floor_amount < 0:
        errors.append("salary_floor_amount must be non-negative.")
    if answers.salary_floor_currency not in CURRENCY_OPTIONS:
        errors.append(f"currency '{answers.salary_floor_currency}' not in {CURRENCY_OPTIONS}.")

    if answers.posted_since_window not in WINDOW_OPTIONS:
        errors.append(f"window '{answers.posted_since_window}' not in {WINDOW_OPTIONS}.")
    if answers.webhook_notify_tier not in NOTIFY_TIER_OPTIONS:
        errors.append(f"webhook_notify_tier '{answers.webhook_notify_tier}' "
                      f"not in {NOTIFY_TIER_OPTIONS}.")

    if not answers.interest_description.strip():
        errors.append("interest_description required (wizard field 10a — what roles do you want?).")

    return errors


def to_profile(answers: WizardAnswers) -> Profile:
    """Convert validated wizard answers to a Profile dataclass."""
    return Profile(
        variant=answers.variant,
        eu_include_uk_ie=answers.eu_include_uk_ie,
        home_country=answers.home_country,
        home_state=answers.home_state,
        home_city=answers.home_city,
        work_modes=answers.work_modes,
        search_outside_home=answers.search_outside_home,
        willing_to_relocate=answers.willing_to_relocate,
        accepted_seniority=answers.accepted_seniority,
        salary_floor_amount=answers.salary_floor_amount,
        salary_floor_currency=answers.salary_floor_currency,
        interest_description=answers.interest_description,
        pursue_blockers=answers.pursue_blockers,
        stretch_indicators=answers.stretch_indicators,
        cv_url=answers.cv_url,
        cv_summary=answers.cv_summary,
        posted_since_window=answers.posted_since_window,
        ai50_seed_enabled=answers.ai50_seed_enabled,
        webhook_url=answers.webhook_url,
        webhook_enabled=answers.webhook_enabled,
        webhook_notify_tier=answers.webhook_notify_tier,
    )
