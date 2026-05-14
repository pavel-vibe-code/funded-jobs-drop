"""Cross-source canonical-URL deduplication.

Within a single Discovery run, a job can appear from multiple sources
(e.g., Anthropic surfaced via a16z's Consider feed AND via the user's
Favorites direct ATS fetch). We collapse to one row per canonical URL.

First-wins ordering matters: sources are merged in [Consider, Getro,
Favorites] order, so Consider's richer structured data wins over
Favorites' sparse data when they overlap.
"""
from __future__ import annotations

from discovery.sources.base import DiscoveredJob


def dedup(jobs: list[DiscoveredJob]) -> tuple[list[DiscoveredJob], int]:
    """Dedup by canonical_url. Returns (unique_jobs, num_duplicates_collapsed)."""
    seen: dict[str, DiscoveredJob] = {}
    duplicates = 0
    for j in jobs:
        if not j.canonical_url:
            continue
        if j.canonical_url in seen:
            duplicates += 1
        else:
            seen[j.canonical_url] = j
    return list(seen.values()), duplicates
