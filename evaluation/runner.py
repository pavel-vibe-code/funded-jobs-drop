"""Stub Evaluation for Phase 2 — marks every candidate as Strong/Pursue.

Phase 3 replaces this with the real screener (Pass A) + scorer (Pass B) flow.
Lets us validate end-to-end Discovery → State.write before LLM integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from discovery.sources.base import DiscoveredJob
from state.profile import Profile


def run(candidates: list[DiscoveredJob], profile: Profile,
        run_id: str) -> tuple[list[dict], dict]:
    """Mark every candidate as Strong — Pursue.

    Returns (verdicts_dict_list, metrics_dict).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    profile_hash = profile.profile_hash

    verdicts: list[dict] = []
    for c in candidates:
        verdicts.append({
            "canonical_url":               c.canonical_url,
            "title":                       c.title,
            "company":                     c.company_name,
            "location":                    _format_location(c),
            "match":                       "Strong — Pursue",
            "why_fits":                    "(Phase 2 stub — Phase 3 will populate via LLM scoring)",
            "salary":                      _format_salary(c),
            "seniority":                   c.seniority,
            "posted_at_iso":               c.posted_at.isoformat(),
            "status":                      "New",
            "source_platform":             c.source_platform,
            "vc_source":                   c.vc_source,
            "first_seen_at_iso":           now_iso,
            "last_seen_at_iso":            now_iso,
            "pass_a_verdict":              "keep",
            "pass_a_reason":               "(stub)",
            "pass_b_residency_ok":         True,
            "pass_b_attempts":             1,
            "profile_hash_at_eval":        profile_hash,
            "last_run_id":                 run_id,
            "pursue_blockers_detected":    "",
            "stretch_indicators_detected": "",
        })

    metrics = {
        "pass_a_evaluated":  len(candidates),
        "pass_a_kept":       len(candidates),
        "pass_b_scored":     len(candidates),
        "pursue_count":      len(candidates),
        "consider_count":    0,
        "skim_count":        0,
        "cost_usd":          0.0,
        "evaluation_stub":   True,
    }
    return verdicts, metrics


def _format_location(c: DiscoveredJob) -> str:
    """Combine raw_location + work_mode into a display string."""
    base = c.raw_location[0] if c.raw_location else "Unknown"
    mode_label = {"remote": "Remote", "hybrid": "Hybrid", "on_site": ""}.get(c.work_mode, "")
    return f"{base} ({mode_label})" if mode_label else base


def _format_salary(c: DiscoveredJob) -> str:
    """Format salary as posted (native currency + yearly), or '—' if undisclosed."""
    if not c.salary_disclosed or not c.salary_min_yearly:
        return "—"
    cur = c.salary_currency or "USD"
    cur_sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, cur + " ")

    def fmt(n: int) -> str:
        return f"{n // 1000}k" if n >= 10000 else str(n)

    lo = fmt(c.salary_min_yearly)
    if c.salary_max_yearly and c.salary_max_yearly != c.salary_min_yearly:
        hi = fmt(c.salary_max_yearly)
        return f"{cur_sym}{lo}–{cur_sym}{hi} / yr"
    return f"{cur_sym}{lo} / yr"
