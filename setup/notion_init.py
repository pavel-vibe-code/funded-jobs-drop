"""Notion workspace initialization — creates and validates the 4 DBs.

Idempotent: re-running with existing db_ids validates and patches missing
columns rather than recreating. Schemas are the source of truth here.
"""
from __future__ import annotations

from typing import Optional

from config.vcs import all_vc_names
from state.notion_client import NotionClient, SetupError


# ─── Property-type helpers ─────────────────────────────────────────────

def _select(options: list[tuple[str, str]]) -> dict:
    """options = [(name, color), ...]. Color must be a Notion color name."""
    return {"select": {"options": [{"name": n, "color": c} for n, c in options]}}


def _multi_select(options: list[tuple[str, str]]) -> dict:
    return {"multi_select": {"options": [{"name": n, "color": c} for n, c in options]}}


# Shared option lists
SENIORITY_OPTIONS = [
    ("entry", "default"), ("mid", "default"), ("senior", "default"),
    ("staff", "default"), ("principal", "default"), ("executive", "default"),
]

SOURCE_PLATFORM_OPTIONS = [
    ("Consider", "blue"), ("Getro", "purple"), ("Favorites", "green"),
]

VC_SOURCE_OPTIONS = [(name, "default") for name in all_vc_names()]


# ─── Tracker DB schema (26 properties) ─────────────────────────────────

TRACKER_TITLE = "Tracker"

def _tracker_properties() -> dict:
    return {
        # User-facing (12)
        "Title": {"title": {}},
        "Company": {"rich_text": {}},
        "Location": {"rich_text": {}},
        "Match": _select([
            ("Strong — Pursue",   "green"),
            ("Decent — Consider", "yellow"),
            ("Stretch — Skim",    "gray"),
        ]),
        "Why fits": {"rich_text": {}},
        "Salary": {"rich_text": {}},
        "Seniority": _select(SENIORITY_OPTIONS),
        "Posted": {"date": {}},
        "Apply": {"url": {}},
        "Status": _select([
            ("New",                 "blue"),
            ("Reviewed",            "yellow"),
            ("Applied",             "green"),
            ("Not interested",      "gray"),
            ("Closed",              "default"),
            ("jd_fetch_failed",     "orange"),
            ("dropped_by_pass_a",   "default"),
        ]),
        "Match quality": _select([
            ("OK",       "default"),
            ("Feedback", "red"),
        ]),
        "Feedback": {"rich_text": {}},

        # Hidden state columns (14)
        "source_platform":           _select(SOURCE_PLATFORM_OPTIONS),
        "vc_source":                 _select(VC_SOURCE_OPTIONS),
        "first_seen_at":             {"date": {}},
        "last_seen_at":              {"date": {}},
        "expires_at":                {"date": {}},
        "closed_at":                 {"date": {}},
        "pass_a_verdict":            _select([
            ("keep",  "green"),
            ("maybe", "yellow"),
            ("drop",  "red"),
        ]),
        "pass_a_reason":             {"rich_text": {}},
        "pass_b_residency_ok":       {"checkbox": {}},
        "pass_b_attempts":           {"number": {}},
        "profile_hash_at_eval":      {"rich_text": {}},
        "last_run_id":               {"rich_text": {}},
        "pursue_blockers_detected":  {"rich_text": {}},
        "stretch_indicators_detected": {"rich_text": {}},
    }


# ─── Profile DB schema (25 fields) ─────────────────────────────────────

PROFILE_TITLE = "Profile"

WORK_MODE_OPTIONS = [
    ("Remote",                       "blue"),
    ("Hybrid",                       "purple"),
    ("Onsite (includes Hybrid)",     "orange"),
]

VARIANT_OPTIONS = [("EU", "blue"), ("US", "green")]

CURRENCY_OPTIONS = [
    (c, "default") for c in
    ("USD", "EUR", "GBP", "CHF", "CAD", "AUD", "PLN", "CZK", "SEK")
]

WINDOW_OPTIONS = [
    ("1 week",  "default"),
    ("2 weeks", "default"),
    ("1 month", "default"),
]


def _profile_properties() -> dict:
    return {
        "Name": {"title": {}},
        # Region & location
        "variant":             _select(VARIANT_OPTIONS),
        "eu_include_uk_ie":    {"checkbox": {}},
        "home_country":        {"rich_text": {}},
        "home_state":          {"rich_text": {}},
        "home_city":           {"rich_text": {}},
        # Work mode & relocation
        "work_modes":          _multi_select(WORK_MODE_OPTIONS),
        "search_outside_home": {"checkbox": {}},
        "willing_to_relocate": {"checkbox": {}},
        # Seniority & salary
        "accepted_seniority":  _multi_select(SENIORITY_OPTIONS),
        "salary_floor_amount": {"number": {}},
        "salary_floor_currency": _select(CURRENCY_OPTIONS),
        # Scoring criteria (free text)
        "interest_description": {"rich_text": {}},
        "pursue_blockers":      {"rich_text": {}},
        "stretch_indicators":   {"rich_text": {}},
        # CV
        "cv_url":     {"url": {}},
        "cv_summary": {"rich_text": {}},
        # Exclusions
        "excluded_companies":  _multi_select([]),  # populated dynamically at runtime
        "excluded_industries": _multi_select([]),
        # qa-written
        "learned_exclusions": {"rich_text": {}},
        "learned_examples":   {"rich_text": {}},
        # Settings
        "posted_since_window": _select(WINDOW_OPTIONS),
        "ai50_seed_enabled":   {"checkbox": {}},
        "webhook_url":         {"url": {}},
        "webhook_enabled":     {"checkbox": {}},
        # System
        "profile_hash": {"rich_text": {}},
    }


# ─── Favorites DB schema (6 properties) ────────────────────────────────

FAVORITES_TITLE = "Favorites"

ATS_TYPE_OPTIONS = [(t, "default") for t in (
    "greenhouse", "ashby", "lever", "comeet", "teamtailor", "homerun",
    "smartrecruiters", "workable", "recruitee", "personio", "bamboohr",
)]

FAVORITE_SOURCE_OPTIONS = [
    ("user",       "blue"),
    ("seed:ai50",  "purple"),
]


def _favorites_properties() -> dict:
    return {
        "Name":        {"title": {}},
        "careers_url": {"url": {}},
        "ats_type":    _select(ATS_TYPE_OPTIONS),
        "ats_slug":    {"rich_text": {}},
        "source":      _select(FAVORITE_SOURCE_OPTIONS),
        "active":      {"checkbox": {}},
    }


# ─── Runs DB schema (17 properties) ────────────────────────────────────

RUNS_TITLE = "Runs"


def _runs_properties() -> dict:
    return {
        "Name": {"title": {}},
        # User-visible
        "started_at":      {"date": {}},
        "variant":         _select(VARIANT_OPTIONS),
        "summary":         {"rich_text": {}},
        "total_new":       {"number": {}},
        "pursue_count":    {"number": {}},
        "consider_count":  {"number": {}},
        "skim_count":      {"number": {}},
        # Hidden
        "run_id":              {"rich_text": {}},
        "duration_s":          {"number": {}},
        "cost_usd":            {"number": {}},
        "discovery_total":     {"number": {}},
        "after_filters":       {"number": {}},
        "pass_a_evaluated":    {"number": {}},
        "pass_b_scored":       {"number": {}},
        "errors_count":        {"number": {}},
        "errors_summary":      {"rich_text": {}},
        "jsonl_log":           {"rich_text": {}},
    }


# ─── DB registry: name → (title, properties_fn) ────────────────────────

DB_REGISTRY = {
    "tracker":   (TRACKER_TITLE,   _tracker_properties),
    "profile":   (PROFILE_TITLE,   _profile_properties),
    "favorites": (FAVORITES_TITLE, _favorites_properties),
    "runs":      (RUNS_TITLE,      _runs_properties),
}


# ─── Public API ────────────────────────────────────────────────────────

def create_all(client: NotionClient, parent_page_id: str) -> dict[str, str]:
    """Create all 4 DBs under the parent page.

    Returns {'tracker_db_id': ..., 'profile_db_id': ..., 'favorites_db_id': ..., 'runs_db_id': ...}.
    """
    db_ids: dict[str, str] = {}
    for name, (title, props_fn) in DB_REGISTRY.items():
        db_id = client.create_database(parent_page_id, title, props_fn())
        # v1.5 lesson: confirm exactly one data_source on the new DB
        client.validate_single_data_source(db_id)
        db_ids[f"{name}_db_id"] = db_id
    return db_ids


def validate_or_patch(client: NotionClient, db_ids: dict[str, str]) -> dict[str, list[str]]:
    """For each existing DB, validate schema and patch missing properties.

    Args:
        db_ids: {'tracker_db_id': '...', 'profile_db_id': '...', ...}

    Returns:
        {db_name: [added_property_names]} for any patches applied.

    Raises:
        SetupError if a DB has unexpected data source count.
    """
    patches: dict[str, list[str]] = {}
    for name, (_title, props_fn) in DB_REGISTRY.items():
        db_id = db_ids.get(f"{name}_db_id")
        if not db_id:
            raise SetupError(f"Missing {name}_db_id in workspace config")

        client.validate_single_data_source(db_id)
        current = client.get_database(db_id)
        current_props = set(current.get("properties", {}).keys())
        target_props = props_fn()
        missing = [p for p in target_props if p not in current_props]

        if missing:
            client.patch_database_properties(db_id, {p: target_props[p] for p in missing})
            patches[name] = missing
    return patches
