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
from state.favorites import set_active, update_ats_config


def _slug_is_live(ats_type: str, slug: str) -> tuple[bool, str]:
    """Probe an ATS slug. Returns (is_live, error_or_empty)."""
    ids, err = active_ids_for(ats_type, slug)
    if err:
        return False, err
    return True, ""


def enable() -> dict:
    """Insert/reconcile seed rows. Probes each ATS slug before writing.

    Reconcile semantics (idempotent — re-running converges on AI50_SEED):
      - If a seed entry's NAME exists in Favorites but its ATS slug drifted
        (company moved Ashby → Greenhouse, slug renamed, etc.) → update the
        existing row in place. Avoids creating duplicate stale rows.
      - If a seed entry is new → add.
      - If a seed:ai50 favorite no longer exists in AI50_SEED (e.g. Surge AI
        was removed in v0.1.4) → deactivate the row.
      - If an entry's current slug doesn't probe live → skip (don't write).

    Returns counts: added / reactivated / updated_slug / deactivated_removed
    / skipped_user_owned / skipped_invalid / invalid_slugs (list of strings).
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return {"added": len(AI50_SEED), "reactivated": 0, "updated_slug": 0,
                "deactivated_removed": 0, "skipped_user_owned": 0,
                "skipped_invalid": 0, "invalid_slugs": []}

    all_favs = favorites_read_all()
    # Identity is by NAME — slug + ats_type can drift over time. User-owned
    # rows (source != "seed:ai50") are matched separately and protected.
    seed_by_name = {f.name: f for f in all_favs if f.source == "seed:ai50"}
    user_slugs = {f.ats_slug for f in all_favs if f.source != "seed:ai50"}

    counts: dict = {"added": 0, "reactivated": 0, "updated_slug": 0,
                    "deactivated_removed": 0, "skipped_user_owned": 0,
                    "skipped_invalid": 0, "invalid_slugs": []}

    seed_names = {entry["name"] for entry in AI50_SEED}

    for entry in AI50_SEED:
        name = entry["name"]
        slug = entry["ats_slug"]
        ats = entry["ats_type"]

        # Protect user-owned rows that happen to share the same slug.
        if slug in user_slugs:
            counts["skipped_user_owned"] += 1
            continue

        live, err = _slug_is_live(ats, slug)
        if not live:
            counts["skipped_invalid"] += 1
            counts["invalid_slugs"].append(f"{name} ({ats}:{slug}): {err}")
            print(f"  [ai50_seed] skipping {name!r} — {ats} slug "
                  f"{slug!r} not live ({err}).")
            continue

        existing = seed_by_name.get(name)
        if existing is None:
            favorites_add(Favorite(
                name=name, careers_url=entry["careers_url"],
                ats_type=ats, ats_slug=slug,
                source="seed:ai50", active=True,
            ))
            counts["added"] += 1
            continue

        # Existing seed row found by name. Check if (ats_type, slug) drifted.
        if existing.ats_type != ats or existing.ats_slug != slug:
            update_ats_config(existing.page_id, ats, slug)
            counts["updated_slug"] += 1
            print(f"  [ai50_seed] updated {name!r}: "
                  f"({existing.ats_type}:{existing.ats_slug}) → ({ats}:{slug})")
        elif not existing.active:
            set_active(existing.page_id, True)
            counts["reactivated"] += 1

    # Deactivate seed rows whose name was removed from AI50_SEED.
    for fav in all_favs:
        if fav.source == "seed:ai50" and fav.active and fav.name not in seed_names:
            set_active(fav.page_id, False)
            counts["deactivated_removed"] += 1
            print(f"  [ai50_seed] deactivated {fav.name!r} — no longer in seed list")

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
