"""AI-50 supplement loader — inserts/activates/deactivates the 14 seed rows.

Triggered when the user toggles `ai50_seed_enabled` in Profile (during setup
or via /fd-settings). Never overwrites user-owned rows with the same slug.
"""
from __future__ import annotations

import os

from config.ai50_seed import AI50_SEED
from state.favorites import Favorite, add as favorites_add
from state.favorites import read_all as favorites_read_all
from state.favorites import set_active


def enable() -> dict[str, int]:
    """Insert all 14 seed rows (or reactivate if present).

    Returns counts: {'added', 'reactivated', 'skipped_user_owned'}.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return {"added": len(AI50_SEED), "reactivated": 0, "skipped_user_owned": 0}

    existing = {f.ats_slug: f for f in favorites_read_all()}
    counts = {"added": 0, "reactivated": 0, "skipped_user_owned": 0}

    for entry in AI50_SEED:
        slug = entry["ats_slug"]
        existing_row = existing.get(slug)
        if existing_row is None:
            favorites_add(Favorite(
                name=entry["name"],
                careers_url=entry["careers_url"],
                ats_type=entry["ats_type"],
                ats_slug=slug,
                source="seed:ai50",
                active=True,
            ))
            counts["added"] += 1
        elif existing_row.source == "seed:ai50":
            if not existing_row.active:
                set_active(existing_row.page_id, True)
                counts["reactivated"] += 1
        else:
            # User-owned row with the same slug — don't touch
            counts["skipped_user_owned"] += 1

    return counts


def disable() -> int:
    """Deactivate all source=seed:ai50 rows. Returns count deactivated."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return len(AI50_SEED)

    deactivated = 0
    for f in favorites_read_all():
        if f.source == "seed:ai50" and f.active:
            set_active(f.page_id, False)
            deactivated += 1
    return deactivated
