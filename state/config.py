"""Workspace configuration — loads Notion DB IDs from settings.local.json.

Setup writes the DB IDs after creating the workspace; runtime reads them.
Single source of truth: ~/.claude/settings.local.json under the 'funded-drop' key.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from state.notion_client import AuthError


@dataclass
class WorkspaceConfig:
    notion_token: str
    parent_page_id: str
    tracker_db_id: str
    profile_db_id: str
    favorites_db_id: str
    runs_db_id: str


SETTINGS_PATH = Path.home() / ".claude" / "settings.local.json"
SETTINGS_KEY = "funded-drop"

REQUIRED_KEYS = (
    "notion_token", "parent_page_id",
    "tracker_db_id", "profile_db_id", "favorites_db_id", "runs_db_id",
)

# Canonical DB titles created by /fd-setup. Used by name→id discovery so a
# Cloud Routine user only needs to set FD_NOTION_TOKEN + FD_PARENT_PAGE_ID;
# the four DB IDs resolve at runtime. Kept here (not imported from
# setup/notion_init) to avoid a runtime → setup/ import edge at fire time.
_CANONICAL_DB_TITLES = {
    "Tracker":   "tracker_db_id",
    "Profile":   "profile_db_id",
    "Favorites": "favorites_db_id",
    "Runs":      "runs_db_id",
}

# Per-process cache so repeated load_workspace() calls in one fire don't
# re-discover. Containers are per-fire ephemeral, so no need for cross-fire
# caching — Notion is fast enough and IDs are stable across fires anyway.
_discovery_cache: dict[str, dict[str, str]] = {}


def _resolve_via_discovery(token: str, parent_page_id: str) -> "WorkspaceConfig":
    """Look up DB IDs by canonical name from the children of parent_page_id."""
    from state.notion_client import NotionClient

    cached = _discovery_cache.get(parent_page_id)
    if cached is None:
        client = NotionClient(token)
        children = client.list_child_databases(parent_page_id)
        cached = {}
        for title, cfg_key in _CANONICAL_DB_TITLES.items():
            if title not in children:
                raise AuthError(
                    f"DB titled {title!r} not found under parent page "
                    f"{parent_page_id}. Run /fd-setup first (or set "
                    f"FD_{cfg_key.upper()} explicitly)."
                )
            cached[cfg_key] = children[title]
        _discovery_cache[parent_page_id] = cached

    return WorkspaceConfig(
        notion_token=token, parent_page_id=parent_page_id, **cached,
    )


def load_workspace() -> WorkspaceConfig:
    """Load workspace config. Three paths, in priority order:

    1. **FD_DRY_RUN=1** → return dry-run placeholders (tests).
    2. **Env vars (Cloud Routine path)** — if `FD_NOTION_TOKEN` is set, all six
       values come from `FD_*` env vars. Containers are ephemeral per fire and
       the setup-script context can't materialize files into a known location,
       so the agent runtime reads them directly from the environment.
    3. **`~/.claude/settings.local.json` (local laptop path)** — what /fd-setup
       writes. The persistent-disk form.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return WorkspaceConfig(
            notion_token="dry-run-token",
            parent_page_id="dry-run-parent",
            tracker_db_id="dry-run-tracker",
            profile_db_id="dry-run-profile",
            favorites_db_id="dry-run-favorites",
            runs_db_id="dry-run-runs",
        )

    env_token = os.environ.get("FD_NOTION_TOKEN")
    if env_token:
        parent_page_id = os.environ.get("FD_PARENT_PAGE_ID", "")
        if not parent_page_id:
            raise AuthError("FD_NOTION_TOKEN is set but FD_PARENT_PAGE_ID is missing")

        # Path A — explicit DB IDs from env (skips Notion discovery call).
        explicit = {
            "tracker_db_id":   os.environ.get("FD_TRACKER_DB_ID", ""),
            "profile_db_id":   os.environ.get("FD_PROFILE_DB_ID", ""),
            "favorites_db_id": os.environ.get("FD_FAVORITES_DB_ID", ""),
            "runs_db_id":      os.environ.get("FD_RUNS_DB_ID", ""),
        }
        if all(explicit.values()):
            return WorkspaceConfig(
                notion_token=env_token, parent_page_id=parent_page_id, **explicit,
            )

        # Path B — discover by canonical DB name (matches v1.5 UX: user pastes
        # only token + parent_page_id, runtime resolves the rest).
        return _resolve_via_discovery(env_token, parent_page_id)

    if not SETTINGS_PATH.exists():
        raise AuthError(
            f"Settings file not found at {SETTINGS_PATH} and no FD_* env vars set. "
            f"Run /fd-setup, or set FD_NOTION_TOKEN + FD_PARENT_PAGE_ID + "
            f"FD_{{TRACKER,PROFILE,FAVORITES,RUNS}}_DB_ID for Cloud Routine use."
        )
    try:
        data = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError as e:
        raise AuthError(f"Invalid JSON in {SETTINGS_PATH}: {e}") from e

    fd = data.get(SETTINGS_KEY, {})
    missing = [k for k in REQUIRED_KEYS if not fd.get(k)]
    if missing:
        raise AuthError(
            f"Missing workspace settings keys: {missing}. Run /fd-setup."
        )
    return WorkspaceConfig(**{k: fd[k] for k in REQUIRED_KEYS})


def save_workspace(config: WorkspaceConfig) -> None:
    """Write workspace config to settings.local.json (preserves other top-level keys)."""
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    data[SETTINGS_KEY] = {
        "notion_token": config.notion_token,
        "parent_page_id": config.parent_page_id,
        "tracker_db_id": config.tracker_db_id,
        "profile_db_id": config.profile_db_id,
        "favorites_db_id": config.favorites_db_id,
        "runs_db_id": config.runs_db_id,
    }
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
