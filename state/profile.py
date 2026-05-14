"""Profile DB I/O — single-row DB holding user preferences.

The Profile dataclass mirrors the 25 fields in the Profile DB schema.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from state.config import load_workspace
from state.notion_client import NotionClient, SetupError
from state.properties import (
    extract_checkbox, extract_multi_select, extract_number, extract_select,
    extract_text, extract_url,
    to_checkbox, to_multi_select, to_number, to_select, to_text, to_title, to_url,
)


@dataclass
class Profile:
    # Region & location
    variant: str = "EU"
    eu_include_uk_ie: bool = False
    home_country: str = ""
    home_state: str = ""
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

    # Exclusions (populated by qa + manual edits)
    excluded_companies: list[str] = field(default_factory=list)
    excluded_industries: list[str] = field(default_factory=list)

    # qa-written instructions
    learned_exclusions: str = ""
    learned_examples: str = ""

    # Settings
    posted_since_window: str = "2 weeks"
    ai50_seed_enabled: bool = False
    webhook_url: str = ""
    webhook_enabled: bool = False

    # System
    profile_hash: str = ""
    page_id: str = ""


def compute_hash(profile: Profile) -> str:
    """SHA256-truncated hex digest of profile data (excluding hash + page_id)."""
    d = asdict(profile)
    d.pop("profile_hash", None)
    d.pop("page_id", None)
    payload = json.dumps(d, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _row_to_profile(row: dict) -> Profile:
    """Parse a Notion row into a Profile object."""
    props = row.get("properties", {})
    return Profile(
        variant=extract_select(props.get("variant")) or "EU",
        eu_include_uk_ie=extract_checkbox(props.get("eu_include_uk_ie")),
        home_country=extract_text(props.get("home_country")),
        home_state=extract_text(props.get("home_state")),
        home_city=extract_text(props.get("home_city")),
        work_modes=extract_multi_select(props.get("work_modes")),
        search_outside_home=extract_checkbox(props.get("search_outside_home")),
        willing_to_relocate=extract_checkbox(props.get("willing_to_relocate")),
        accepted_seniority=extract_multi_select(props.get("accepted_seniority")),
        salary_floor_amount=extract_number(props.get("salary_floor_amount")),
        salary_floor_currency=extract_select(props.get("salary_floor_currency")) or "USD",
        interest_description=extract_text(props.get("interest_description")),
        pursue_blockers=extract_text(props.get("pursue_blockers")),
        stretch_indicators=extract_text(props.get("stretch_indicators")),
        cv_url=extract_url(props.get("cv_url")) or "",
        cv_summary=extract_text(props.get("cv_summary")),
        excluded_companies=extract_multi_select(props.get("excluded_companies")),
        excluded_industries=extract_multi_select(props.get("excluded_industries")),
        learned_exclusions=extract_text(props.get("learned_exclusions")),
        learned_examples=extract_text(props.get("learned_examples")),
        posted_since_window=extract_select(props.get("posted_since_window")) or "2 weeks",
        ai50_seed_enabled=extract_checkbox(props.get("ai50_seed_enabled")),
        webhook_url=extract_url(props.get("webhook_url")) or "",
        webhook_enabled=extract_checkbox(props.get("webhook_enabled")),
        profile_hash=extract_text(props.get("profile_hash")),
        page_id=row.get("id", ""),
    )


def _profile_to_props(p: Profile) -> dict:
    """Convert Profile dataclass to Notion property update payload."""
    return {
        "Name": to_title("User Profile"),
        "variant": to_select(p.variant),
        "eu_include_uk_ie": to_checkbox(p.eu_include_uk_ie),
        "home_country": to_text(p.home_country),
        "home_state": to_text(p.home_state),
        "home_city": to_text(p.home_city),
        "work_modes": to_multi_select(p.work_modes),
        "search_outside_home": to_checkbox(p.search_outside_home),
        "willing_to_relocate": to_checkbox(p.willing_to_relocate),
        "accepted_seniority": to_multi_select(p.accepted_seniority),
        "salary_floor_amount": to_number(p.salary_floor_amount),
        "salary_floor_currency": to_select(p.salary_floor_currency),
        "interest_description": to_text(p.interest_description),
        "pursue_blockers": to_text(p.pursue_blockers),
        "stretch_indicators": to_text(p.stretch_indicators),
        "cv_url": to_url(p.cv_url),
        "cv_summary": to_text(p.cv_summary),
        "excluded_companies": to_multi_select(p.excluded_companies),
        "excluded_industries": to_multi_select(p.excluded_industries),
        "learned_exclusions": to_text(p.learned_exclusions),
        "learned_examples": to_text(p.learned_examples),
        "posted_since_window": to_select(p.posted_since_window),
        "ai50_seed_enabled": to_checkbox(p.ai50_seed_enabled),
        "webhook_url": to_url(p.webhook_url),
        "webhook_enabled": to_checkbox(p.webhook_enabled),
        "profile_hash": to_text(p.profile_hash),
    }


def read() -> Profile:
    """Read the single Profile row from Notion."""
    if os.environ.get("FD_DRY_RUN") == "1":
        raise SetupError(
            "Profile.read() requires fixtures in tests/fixtures/notion/profile.json "
            "(not yet created in Phase 1; will be added in test infrastructure phase)"
        )
    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.profile_db_id)

    rows = list(client.query_data_source(ds_id, page_size=2))
    if len(rows) != 1:
        raise SetupError(
            f"Profile DB has {len(rows)} rows; expected exactly 1. "
            "Run /fd-setup --repair."
        )
    return _row_to_profile(rows[0])


def write_initial(profile: Profile) -> str:
    """Create the single Profile row at setup time. Computes hash. Returns page_id."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return "dry-run-profile-page-id"
    profile.profile_hash = compute_hash(profile)
    config = load_workspace()
    client = NotionClient(config.notion_token)
    return client.create_page(config.profile_db_id, _profile_to_props(profile))


def update(**fields) -> None:
    """Update the existing Profile row with given fields. Recomputes hash."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return
    current = read()
    for k, v in fields.items():
        if hasattr(current, k):
            setattr(current, k, v)
    current.profile_hash = compute_hash(current)
    config = load_workspace()
    client = NotionClient(config.notion_token)
    client.update_page(current.page_id, _profile_to_props(current))
