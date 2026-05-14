"""Favorites DB I/O — pinned companies bypassing VC discovery.

Holds user-added favorites (source="user") and AI-50 seed entries
(source="seed:ai50") toggled via Profile.ai50_seed_enabled.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from state.config import load_workspace
from state.notion_client import NotionClient
from state.properties import (
    extract_checkbox, extract_select, extract_text, extract_url,
    to_checkbox, to_select, to_text, to_title, to_url,
)


@dataclass
class Favorite:
    name: str
    careers_url: str
    ats_type: str       # greenhouse / ashby / lever / ...
    ats_slug: str       # adapter-specific identifier
    source: str = "user"   # "user" | "seed:ai50"
    active: bool = True
    page_id: str = ""


def _row_to_favorite(row: dict) -> Favorite:
    """Parse a Notion row into a Favorite object."""
    props = row.get("properties", {})
    return Favorite(
        name=extract_text(props.get("Name")),
        careers_url=extract_url(props.get("careers_url")) or "",
        ats_type=extract_select(props.get("ats_type")) or "",
        ats_slug=extract_text(props.get("ats_slug")),
        source=extract_select(props.get("source")) or "user",
        active=extract_checkbox(props.get("active")),
        page_id=row.get("id", ""),
    )


def _favorite_to_props(f: Favorite) -> dict:
    """Convert Favorite dataclass to Notion property update payload."""
    return {
        "Name": to_title(f.name),
        "careers_url": to_url(f.careers_url),
        "ats_type": to_select(f.ats_type),
        "ats_slug": to_text(f.ats_slug),
        "source": to_select(f.source),
        "active": to_checkbox(f.active),
    }


def add(favorite: Favorite) -> str:
    """Add a new favorite. Returns the new page_id."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return "dry-run-favorite-id"
    config = load_workspace()
    client = NotionClient(config.notion_token)
    return client.create_page(config.favorites_db_id, _favorite_to_props(favorite))


def read_all() -> list[Favorite]:
    """Read all favorites (active and inactive)."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return []
    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.favorites_db_id)
    return [_row_to_favorite(row) for row in client.query_data_source(ds_id)]


def read_active() -> list[Favorite]:
    """Read only active favorites — used by Discovery's favorites source."""
    return [f for f in read_all() if f.active]


def set_active(page_id: str, active: bool) -> None:
    """Toggle the active flag on a favorite. Used by AI-50 seed enable/disable."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return
    config = load_workspace()
    client = NotionClient(config.notion_token)
    client.update_page(page_id, {"active": to_checkbox(active)})
