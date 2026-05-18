"""Discovery orchestrator — fetch sources, dedup, tracker-check, prefilter."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Optional

from discovery.dedup import dedup
from discovery.prefilter import apply as apply_prefilter
from discovery.sources import consider, favorites, getro
from discovery.sources.base import DiscoveredJob
from state.profile import Profile


WINDOW_DAYS_MAP = {"1 week": 7, "2 weeks": 14, "1 month": 30}


def effective_window_days(profile_window_days: int,
                          last_fire_at_epoch: Optional[float]) -> int:
    """Dynamic widening on missed fire. Caps at 30 days.

    If no last fire (cold start) → use profile window as-is.
    If gap within profile window → use profile window.
    If gap exceeds profile window → widen to cover gap + 2-day buffer, capped at 30.
    """
    if last_fire_at_epoch is None:
        return profile_window_days
    gap_days = (time.time() - last_fire_at_epoch) / 86400
    if gap_days <= profile_window_days:
        return profile_window_days
    return min(int(gap_days) + 2, 30)


def run(profile: Profile,
        tracker_known_urls: set[str],
        last_fire_at_epoch: Optional[float] = None) -> tuple[list[DiscoveredJob], dict]:
    """Full Discovery pipeline.

    Args:
        profile: user's profile (variant, filters, salary floor, etc.)
        tracker_known_urls: set of canonical URLs already in Notion Tracker
        last_fire_at_epoch: unix epoch of last successful fire, or None (cold start)

    Returns:
        (candidates, metrics) — candidates pass to Evaluation; metrics go to Runs DB.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return [], {
            "effective_window_days": 14,
            "profile_window_days": 14,
            "discovery_total": 0,
            "cross_source_duplicates": 0,
            "after_dedup": 0,
            "after_tracker_check": 0,
            "prefilter_counts": {},
            "after_prefilter": 0,
            "per_source_counts": {},
            "per_source_urls": {},
            "source_errors": [],
        }

    profile_window = WINDOW_DAYS_MAP.get(profile.posted_since_window, 14)
    effective_days = effective_window_days(profile_window, last_fire_at_epoch)
    since_epoch = int(time.time() - effective_days * 86400)

    # 1. Fetch from all sources (sequential for v0.1.0; parallel later if needed).
    # Each source returns (jobs, errors); errors propagate up so the Runs DB
    # surfaces per-source failures (don't silently swallow Getro 403s etc.).
    all_jobs: list[DiscoveredJob] = []
    source_errors: list[str] = []
    for src_fn in (consider.fetch, getro.fetch):
        jobs, errs = src_fn(profile, since_epoch)
        all_jobs.extend(jobs)
        source_errors.extend(errs)
    # Favorites additionally report how many jobs their pre-discovery region
    # filter dropped — threaded into discovery-metrics for the Runs jsonl_log.
    fav_jobs, fav_errs, fav_region_dropped = favorites.fetch(profile, since_epoch)
    all_jobs.extend(fav_jobs)
    source_errors.extend(fav_errs)
    discovery_total = len(all_jobs)

    # 1b. Per-source URL sets — used by closure detection downstream.
    # Favorites have vc_source=None and are NOT subject to VC-source-based
    # closure (their disappearance from a company's ATS would need
    # per-company tracking, deferred to a later phase).
    per_source_urls: dict[str, set[str]] = defaultdict(set)
    for j in all_jobs:
        if j.vc_source:
            per_source_urls[j.vc_source].add(j.canonical_url)

    # 2. Cross-source dedup (first-wins; Consider before Getro before Favorites)
    deduped, num_dups = dedup(all_jobs)

    # 3. Tracker check: drop URLs we've already evaluated
    new_candidates = [j for j in deduped if j.canonical_url not in tracker_known_urls]

    # 4. S2-S9 prefilter
    survivors, prefilter_counts = apply_prefilter(new_candidates, profile)

    metrics = {
        "effective_window_days": effective_days,
        "profile_window_days": profile_window,
        "discovery_total": discovery_total,
        "cross_source_duplicates": num_dups,
        "after_dedup": len(deduped),
        "after_tracker_check": len(new_candidates),
        "prefilter_counts": prefilter_counts,
        "after_prefilter": len(survivors),
        "per_source_counts": {src: len(urls) for src, urls in per_source_urls.items()},
        # per_source_urls is consumed by orchestrator for closure detection;
        # convert to sorted lists for JSON serialization upstream.
        "per_source_urls": {src: sorted(urls) for src, urls in per_source_urls.items()},
        "favorites_region_dropped": fav_region_dropped,
        "source_errors": source_errors,
    }
    return survivors, metrics
