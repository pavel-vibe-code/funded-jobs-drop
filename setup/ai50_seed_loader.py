"""AI-50 supplement loader — inserts/activates/deactivates the 14 seed rows.

Triggered when the user toggles `ai50_seed_enabled` in Profile (during setup
or via /fd-settings). Never overwrites user-owned rows with the same slug.

v0.1.3: enable() now probes each ATS slug against its adapter BEFORE writing.
Invalid slugs (404 / DNS error / etc.) are skipped and logged. This prevents
broken Favorites rows from accumulating in Notion when companies move ATSes
or the seed list drifts.
"""
from __future__ import annotations

import os

from config.ai50_seed import AI50_SEED
from evaluation.ats_adapters import active_ids_for
from state.favorites import Favorite, add as favorites_add
from state.favorites import read_all as favorites_read_all
from state.favorites import set_active


def _slug_is_live(ats_type: str, slug: str) -> tuple[bool, str]:
    """Probe an ATS slug. Returns (is_live, error_or_empty)."""
    ids, err = active_ids_for(ats_type, slug)
    if err:
        return False, err
    return True, ""


def enable() -> dict:
    """Insert seed rows that probe live (or reactivate if present).

    Returns counts: {'added', 'reactivated', 'skipped_user_owned', 'skipped_invalid', 'invalid_slugs'}.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return {"added": len(AI50_SEED), "reactivated": 0,
                "skipped_user_owned": 0, "skipped_invalid": 0, "invalid_slugs": []}

    existing = {f.ats_slug: f for f in favorites_read_all()}
    counts: dict = {"added": 0, "reactivated": 0,
                    "skipped_user_owned": 0, "skipped_invalid": 0,
                    "invalid_slugs": []}

    for entry in AI50_SEED:
        slug = entry["ats_slug"]
        ats = entry["ats_type"]

        live, err = _slug_is_live(ats, slug)
        if not live:
            counts["skipped_invalid"] += 1
            counts["invalid_slugs"].append(f"{entry['name']} ({ats}:{slug}): {err}")
            print(f"  [ai50_seed] skipping {entry['name']!r} — {ats} slug "
                  f"{slug!r} not live ({err}). Update config/ai50_seed.py.")
            continue

        existing_row = existing.get(slug)
        if existing_row is None:
            favorites_add(Favorite(
                name=entry["name"],
                careers_url=entry["careers_url"],
                ats_type=ats,
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
