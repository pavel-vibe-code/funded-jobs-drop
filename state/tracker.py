"""Tracker DB I/O — evaluated-jobs store and primary user surface."""
from __future__ import annotations

import os
from dataclasses import dataclass

from state.config import load_workspace
from state.notion_client import NotionClient
from state.properties import (
    extract_checkbox, extract_number, extract_select, extract_text, extract_url,
    to_checkbox, to_date, to_number, to_select, to_text, to_title, to_url,
)


@dataclass
class TrackerRow:
    """Minimal Tracker row representation — just what's needed for dedup + retry logic."""
    canonical_url: str
    status: str
    profile_hash_at_eval: str
    pass_b_attempts: int
    page_id: str


def read_url_index(filter_status_not: str = "Closed") -> dict[str, TrackerRow]:
    """Return {canonical_url: TrackerRow} for non-Closed rows.

    Server-side filter to keep query cheap at scale.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return {}

    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.tracker_db_id)

    notion_filter = {
        "property": "Status",
        "select": {"does_not_equal": filter_status_not},
    }

    index: dict[str, TrackerRow] = {}
    for row in client.query_data_source(ds_id, filter=notion_filter, page_size=100):
        props = row.get("properties", {})
        url = extract_url(props.get("Apply"))
        if not url:
            continue
        index[url] = TrackerRow(
            canonical_url=url,
            status=extract_select(props.get("Status")) or "New",
            profile_hash_at_eval=extract_text(props.get("profile_hash_at_eval")),
            pass_b_attempts=int(extract_number(props.get("pass_b_attempts")) or 0),
            page_id=row.get("id", ""),
        )
    return index


def write_evaluated(verdicts: list[dict]) -> dict[str, int]:
    """Create Tracker rows for each verdict. Returns {written, failed} counts.

    Each verdict dict contains the fields needed for both user-facing and
    hidden state columns. See _verdict_to_props for the expected shape.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return {"written": len(verdicts), "failed": 0}

    config = load_workspace()
    client = NotionClient(config.notion_token)

    written = 0
    failed = 0
    for v in verdicts:
        try:
            client.create_page(config.tracker_db_id, _verdict_to_props(v))
            written += 1
        except Exception as e:
            print(f"[tracker.write_evaluated] failed for {v.get('canonical_url')}: {e}")
            failed += 1
    return {"written": written, "failed": failed}


def update_status(page_id: str, status: str, closed_at_iso: str = "") -> None:
    """Update a row's Status (and closed_at when transitioning to Closed)."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return
    config = load_workspace()
    client = NotionClient(config.notion_token)
    props = {"Status": to_select(status)}
    if status == "Closed" and closed_at_iso:
        props["closed_at"] = to_date(closed_at_iso)
    client.update_page(page_id, props)


def _verdict_to_props(v: dict) -> dict:
    """Convert a verdict dict to Notion Tracker row properties."""
    vc_source = v.get("vc_source")
    pass_a_verdict = v.get("pass_a_verdict")
    return {
        # User-facing
        "Title":         to_title(v.get("title", "")),
        "Company":       to_text(v.get("company", "")),
        "Location":      to_text(v.get("location", "")),
        "Match":         to_select(v.get("match", "Decent — Consider")),
        "Why fits":      to_text(v.get("why_fits", "")),
        "Salary":        to_text(v.get("salary", "—")),
        "Seniority":     to_select(v.get("seniority")) if v.get("seniority") else {"select": None},
        "Posted":        to_date(v.get("posted_at_iso")),
        "Apply":         to_url(v.get("canonical_url")),
        "Status":        to_select(v.get("status", "New")),
        "Match quality": to_select("OK"),
        "Feedback":      to_text(""),
        # Hidden
        "source_platform":             to_select(v.get("source_platform")),
        "vc_source":                   to_select(vc_source) if vc_source else {"select": None},
        "first_seen_at":               to_date(v.get("first_seen_at_iso")),
        "last_seen_at":                to_date(v.get("last_seen_at_iso")),
        "pass_a_verdict":              to_select(pass_a_verdict) if pass_a_verdict else {"select": None},
        "pass_a_reason":               to_text(v.get("pass_a_reason", "")),
        "pass_b_residency_ok":         to_checkbox(v.get("pass_b_residency_ok", False)),
        "pass_b_attempts":             to_number(v.get("pass_b_attempts", 0)),
        "profile_hash_at_eval":        to_text(v.get("profile_hash_at_eval", "")),
        "last_run_id":                 to_text(v.get("last_run_id", "")),
        "pursue_blockers_detected":    to_text(v.get("pursue_blockers_detected", "")),
        "stretch_indicators_detected": to_text(v.get("stretch_indicators_detected", "")),
    }
