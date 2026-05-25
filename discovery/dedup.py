"""Cross-source canonical-URL deduplication.

Within a single Discovery run, a job can appear from multiple sources
(e.g., Anthropic surfaced via a16z's Consider feed AND via the user's
Favorites direct ATS fetch). We collapse to one row per canonical URL.

First-wins ordering matters: sources are merged in [Consider, Getro,
Favorites] order, so Consider's richer structured data wins over
Favorites' sparse data when they overlap.

A secondary (company, title) dedup catches the LinkedIn multi-posting
pattern where a company publishes the same role N times, each receiving
a unique job ID and therefore a unique URL. First-seen wins (source
priority ordering is preserved from the URL-dedup pass).
"""
from __future__ import annotations

from discovery.sources.base import DiscoveredJob


def dedup(jobs: list[DiscoveredJob]) -> tuple[list[DiscoveredJob], int]:
    """Dedup by canonical_url then by (company_name, title).

    Returns (unique_jobs, num_duplicates_collapsed).
    """
    # Pass 1: URL dedup
    seen_url: dict[str, DiscoveredJob] = {}
    duplicates = 0
    for j in jobs:
        if not j.canonical_url:
            continue
        if j.canonical_url in seen_url:
            duplicates += 1
        else:
            seen_url[j.canonical_url] = j

    # Pass 2: (company, title) dedup — catches same role posted multiple times
    seen_title: dict[tuple[str, str], DiscoveredJob] = {}
    for j in seen_url.values():
        key = (j.company_name.strip().lower(), j.title.strip().lower())
        if key in seen_title:
            duplicates += 1
        else:
            seen_title[key] = j

    return list(seen_title.values()), duplicates
