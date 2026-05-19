"""Runs DB I/O — per-fire summary + metrics row.

Each fire writes exactly one Runs row at the end. Used for:
  - User-facing summary of what the fire produced (visible in default view)
  - Missed-fire detection (orchestrator reads last successful started_at)
  - Future dev observability (jsonl_log column holds the full trace)
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from state.config import load_workspace
from state.notion_client import NotionClient
from state.properties import (
    extract_date, extract_text,
    to_date, to_number, to_select, to_text, to_text_chunked, to_title,
)


def create(run_id: str,
           started_at_iso: str,
           variant: str,
           summary: str,
           metrics: dict,
           jsonl_log: str = "") -> str:
    """Create a Runs row. Returns page_id."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return "dry-run-runs-row"

    config = load_workspace()
    client = NotionClient(config.notion_token)

    # Display name: "EU run · 2026-05-14 09:00"
    name = f"{variant} run · {started_at_iso[:16].replace('T', ' ')}"

    # Notion rich_text caps individual chunks at 2000 chars but allows many
    # chunks per property — to_text_chunked() splits at ≤1900-char boundaries.
    # Cap total length at 40kB so we keep the Notion property readable in UI
    # and don't risk hitting the per-property limit.
    capped_log = jsonl_log[:40000] if jsonl_log else ""

    props = {
        "Name":             to_title(name),
        "started_at":       to_date(started_at_iso),
        "variant":          to_select(variant),
        "summary":          to_text(summary),
        "total_new":        to_number(metrics.get("total_new", 0)),
        "pursue_count":     to_number(metrics.get("pursue_count", 0)),
        "consider_count":   to_number(metrics.get("consider_count", 0)),
        "skim_count":       to_number(metrics.get("skim_count", 0)),
        "run_id":           to_text(run_id),
        "duration_s":       to_number(metrics.get("duration_s", 0)),
        "cost_usd":         to_number(metrics.get("cost_usd", 0)),
        "discovery_total":  to_number(metrics.get("discovery_total", 0)),
        "after_filters":    to_number(metrics.get("after_prefilter", 0)),
        "pass_a_evaluated": to_number(metrics.get("pass_a_evaluated", 0)),
        "pass_b_scored":    to_number(metrics.get("pass_b_scored", 0)),
        "errors_count":     to_number(metrics.get("errors_count", 0)),
        "errors_summary":   to_text(metrics.get("errors_summary", "")),
        "jsonl_log":        to_text_chunked(capped_log),
    }
    return client.create_page(config.runs_db_id, props)


def read_last_successful() -> Optional[dict]:
    """Return the most recent Runs row, or None if no runs exist yet."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return None

    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.runs_db_id)

    sorts = [{"property": "started_at", "direction": "descending"}]
    for row in client.query_data_source(ds_id, sorts=sorts, page_size=1):
        return row
    return None


def get_last_fire_epoch() -> Optional[float]:
    """Return unix epoch of the most recent fire's started_at, or None.

    Used by Discovery for missed-fire window widening.
    """
    row = read_last_successful()
    if not row:
        return None
    iso = extract_date(row.get("properties", {}).get("started_at"))
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None
