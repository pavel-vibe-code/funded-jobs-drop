"""DiscoveredJob dataclass + Source protocol.

The unified shape flowing from Discovery sources → dedup → prefilter → Evaluation.
Each source adapter (consider.py, getro.py, favorites.py) emits a list of these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional, Protocol


WorkMode = Literal["remote", "hybrid", "on_site"]
Region = Literal["EU", "US", "UK_IE", "GLOBAL_REMOTE", "OTHER"]
Seniority = Literal["entry", "mid", "senior", "staff", "principal", "executive"]
SourcePlatform = Literal["Consider", "Getro", "Favorites"]


# Vocabulary mapping for source-specific seniority strings.
# Used by sources to normalize to the canonical 6-value enum.
SENIORITY_MAP: dict[str, Seniority] = {
    # Consider's jobSeniorityIds vocabulary
    "entry": "entry", "junior": "entry", "intern": "entry", "internship": "entry",
    "mid": "mid", "mid-level": "mid", "mid_level": "mid",
    "senior": "senior",
    "staff": "staff",
    "principal": "principal", "lead": "principal",
    "executive": "executive", "director": "executive", "vp": "executive", "cxo": "executive",
    # Getro's seniority vocabulary
    "entry_level": "entry",
}


# Period multipliers for normalizing salary to yearly
PERIOD_TO_YEARLY: dict[str, int] = {
    "year": 1, "yearly": 1, "annual": 1, "annually": 1,
    "month": 12, "monthly": 12,
    "week": 52, "weekly": 52,
    "day": 260, "daily": 260,
    "hour": 2080, "hourly": 2080,
}


@dataclass
class DiscoveredJob:
    """Unified job shape across all discovery sources."""

    # ── Always present (required) ──────────────────────────────────────
    canonical_url: str
    title: str
    company_name: str
    raw_location: list[str]
    work_mode: WorkMode
    posted_at: datetime
    source_platform: SourcePlatform
    raw: dict[str, Any]  # original API response for debug + JD fallback

    # ── Reliably present (>80% coverage) ──────────────────────────────
    company_slug: Optional[str] = None
    skills: list[str] = field(default_factory=list)
    seniority: Optional[Seniority] = None
    industry_tags: list[str] = field(default_factory=list)
    stage: Optional[str] = None

    # ── Computed by Discovery layer ───────────────────────────────────
    region: Region = "OTHER"
    vc_source: Optional[str] = None
    relevance_prior: float = 0.5  # 0–1; Consider populates from its score; others use 0.5

    # ── Often present (50–95% in at least one source) ─────────────────
    normalized_locations: list[str] = field(default_factory=list)
    company_domain: Optional[str] = None
    departments: list[str] = field(default_factory=list)
    job_functions: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)

    # ── Sparse but valuable (<20% coverage) ───────────────────────────
    company_size: Optional[int] = None
    company_size_is_bucket: bool = False  # True when source is Getro (bucket index, not count)
    salary_min_yearly: Optional[int] = None
    salary_max_yearly: Optional[int] = None
    salary_currency: Optional[str] = None
    salary_disclosed: bool = False
    min_years_exp: Optional[int] = None
    manager: Optional[bool] = None
    offers_equity: Optional[bool] = None

    # ── Source-specific job ID (for in-source dedup if ever needed) ───
    source_job_id: Optional[str] = None

    # ── Favorites: full JD text when the ATS listing already carries it ──
    # (Ashby / Greenhouse / Lever). When set, the JD-fetch stage uses it
    # directly and skips the per-job HTTP fetch. None for VC jobs and for
    # Favorites whose ATS listing has no description (Recruitee, Workday).
    jd_text: Optional[str] = None


def normalize_seniority(value: Optional[str]) -> Optional[Seniority]:
    """Map a source-specific seniority string to canonical enum, or None if unknown."""
    if not value:
        return None
    return SENIORITY_MAP.get(value.lower().strip())


def to_yearly(amount: Optional[float], period: Optional[str]) -> Optional[int]:
    """Convert a salary amount + period to yearly integer (or None if missing)."""
    if amount is None or period is None:
        return None
    multiplier = PERIOD_TO_YEARLY.get(period.lower().strip())
    if multiplier is None:
        return None
    return int(amount * multiplier)


class Source(Protocol):
    """Protocol every discovery source adheres to.

    Sources are stateless modules. Each one fetches its slice of the universe
    independently, returning a flat list of DiscoveredJob objects. The
    discovery runner combines them and runs dedup + prefilter downstream.
    """

    def fetch(self, profile: Any, since_epoch: int) -> list[DiscoveredJob]:
        """Fetch jobs posted after since_epoch, filtered by profile's variant.

        Args:
            profile: state.profile.Profile object (has variant, exclusions, etc.)
            since_epoch: unix epoch — sources walk back from now until this
        Returns:
            List of DiscoveredJob objects.
        """
        ...
