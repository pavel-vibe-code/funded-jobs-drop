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


def load_workspace() -> WorkspaceConfig:
    """Load workspace config. Returns dry-run placeholders if FD_DRY_RUN=1."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return WorkspaceConfig(
            notion_token="dry-run-token",
            parent_page_id="dry-run-parent",
            tracker_db_id="dry-run-tracker",
            profile_db_id="dry-run-profile",
            favorites_db_id="dry-run-favorites",
            runs_db_id="dry-run-runs",
        )
    if not SETTINGS_PATH.exists():
        raise AuthError(
            f"Settings file not found at {SETTINGS_PATH}. Run /fd-setup first."
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
