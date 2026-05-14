"""Tracker DB I/O — evaluated-jobs store and primary user surface."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from state.config import load_workspace
from state.notion_client import NotionClient
from state.properties import (
    extract_checkbox, extract_number, extract_select, extract_text, extract_url,
    to_checkbox, to_date, to_number, to_select, to_text, to_title, to_url,
)


@dataclass
class TrackerRow:
    """Minimal Tracker row representation — dedup + closure + retry logic."""
    canonical_url: str
    status: str
    profile_hash_at_eval: str
    pass_b_attempts: int
    page_id: str
    vc_source: Optional[str] = None
    source_platform: Optional[str] = None


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
            vc_source=extract_select(props.get("vc_source")),
            source_platform=extract_select(props.get("source_platform")),
        )
    return index


def mark_closed_batch(rows: list[TrackerRow]) -> dict:
    """Mark each row as Closed in Notion. Errors per-row are non-fatal.

    Returns {closed: int, failed: int, errors: list[str]}.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    closed = 0
    failed = 0
    errors: list[str] = []

    if os.environ.get("FD_DRY_RUN") == "1":
        return {"closed": len(rows), "failed": 0, "errors": []}

    for row in rows:
        try:
            update_status(row.page_id, "Closed", closed_at_iso=now_iso)
            closed += 1
        except Exception as e:
            failed += 1
            errors.append(f"{row.canonical_url}: {type(e).__name__}: {e}")
    return {"closed": closed, "failed": failed, "errors": errors}


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


def read_feedback_rows(limit: int = 200) -> list[dict]:
    """Return Tracker rows where the user has signalled feedback.

    A row qualifies if Match quality != "OK" (the default) OR Feedback is non-empty.
    Used by the qa agent to refine learned_exclusions + learned_examples.

    Returns plain dicts (not TrackerRow) since the qa agent needs richer fields
    than the dedup-focused TrackerRow carries.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return []

    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.tracker_db_id)

    # Notion `or` filter: Match quality != OK OR Feedback is not empty
    notion_filter = {
        "or": [
            {"property": "Match quality", "select": {"does_not_equal": "OK"}},
            {"property": "Feedback", "rich_text": {"is_not_empty": True}},
        ],
    }

    rows: list[dict] = []
    for row in client.query_data_source(ds_id, filter=notion_filter, page_size=100):
        props = row.get("properties", {})
        rows.append({
            "page_id":            row.get("id", ""),
            "canonical_url":      extract_url(props.get("Apply")) or "",
            "title":              extract_text(props.get("Title")),
            "company":            extract_text(props.get("Company")),
            "match":              extract_select(props.get("Match")) or "",
            "match_quality":      extract_select(props.get("Match quality")) or "OK",
            "feedback":           extract_text(props.get("Feedback")),
            "why_fits":           extract_text(props.get("Why fits")),
            "pursue_blockers":    extract_text(props.get("pursue_blockers_detected")),
            "stretch_indicators": extract_text(props.get("stretch_indicators_detected")),
        })
        if len(rows) >= limit:
            break
    return rows


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


def read_rows_for_rescore(mode: str, current_profile_hash: str = "") -> list[dict]:
    """Return Tracker rows eligible for rescore, per selection mode.

    Modes:
      "failed"  → Status == jd_fetch_failed
      "stale"   → profile_hash_at_eval != current_profile_hash
      "flagged" → Match quality in {Wrong fit, Great match}

    Returns rich dicts (not TrackerRow) with the fields rescore needs:
    page_id, canonical_url, title, company, location, seniority, vc_source,
    source_platform, match (prior), pass_b_attempts.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return []

    config = load_workspace()
    client = NotionClient(config.notion_token)
    ds_id = client.validate_single_data_source(config.tracker_db_id)

    if mode == "failed":
        notion_filter = {"property": "Status",
                         "select": {"equals": "jd_fetch_failed"}}
    elif mode == "stale":
        if not current_profile_hash:
            raise ValueError("stale mode requires current_profile_hash")
        notion_filter = {
            "and": [
                {"property": "Status",
                 "select": {"does_not_equal": "Closed"}},
                {"property": "profile_hash_at_eval",
                 "rich_text": {"does_not_equal": current_profile_hash}},
            ],
        }
    elif mode == "flagged":
        notion_filter = {
            "and": [
                {"property": "Status",
                 "select": {"does_not_equal": "Closed"}},
                {"or": [
                    {"property": "Match quality",
                     "select": {"equals": "Wrong fit"}},
                    {"property": "Match quality",
                     "select": {"equals": "Great match"}},
                ]},
            ],
        }
    else:
        raise ValueError(f"unknown rescore mode: {mode!r}")

    rows: list[dict] = []
    for row in client.query_data_source(ds_id, filter=notion_filter, page_size=100):
        p = row.get("properties", {})
        url = extract_url(p.get("Apply"))
        if not url:
            continue
        rows.append({
            "page_id":            row.get("id", ""),
            "canonical_url":      url,
            "title":              extract_text(p.get("Title")),
            "company":            extract_text(p.get("Company")),
            "location":           extract_text(p.get("Location")),
            "salary":             extract_text(p.get("Salary")),
            "seniority":          extract_select(p.get("Seniority")),
            "match_prior":        extract_select(p.get("Match")) or "",
            "match_quality":      extract_select(p.get("Match quality")) or "OK",
            "feedback":           extract_text(p.get("Feedback")),
            "vc_source":          extract_select(p.get("vc_source")),
            "source_platform":    extract_select(p.get("source_platform")),
            "pass_b_attempts":    int(extract_number(p.get("pass_b_attempts")) or 0),
        })
    return rows


def update_evaluated(page_id: str, verdict: dict) -> None:
    """Update an existing Tracker row with a fresh verdict (Pass B rescore).

    Touches Match, Why fits, Status, pass_b_*, blocker/indicator detections,
    profile_hash_at_eval, last_run_id, last_seen_at.

    Auto-exclude flag: when this rescore detects pursue_blockers AND the user
    hasn't already touched Match quality / Feedback (`existing_*` fields == defaults),
    set Match quality to "Feedback" + write the auto-note. Preserves user
    overrides — if the user previously flipped these to OK / typed their own
    Feedback, we never clobber.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return
    config = load_workspace()
    client = NotionClient(config.notion_token)
    now_iso = datetime.now(timezone.utc).isoformat()
    props: dict = {
        "Match":                       to_select(verdict.get("match", "Decent — Consider")),
        "Why fits":                    to_text(verdict.get("why_fits", "")),
        "Status":                      to_select(verdict.get("status", "New")),
        "last_seen_at":                to_date(now_iso),
        "pass_b_residency_ok":         to_checkbox(bool(verdict.get("pass_b_residency_ok", False))),
        "pass_b_attempts":             to_number(int(verdict.get("pass_b_attempts", 1))),
        "profile_hash_at_eval":        to_text(verdict.get("profile_hash_at_eval", "")),
        "last_run_id":                 to_text(verdict.get("last_run_id", "")),
        "pursue_blockers_detected":    to_text(verdict.get("pursue_blockers_detected", "")),
        "stretch_indicators_detected": to_text(verdict.get("stretch_indicators_detected", "")),
    }

    # Auto-exclude only when the user hasn't already taken a position on this row.
    blockers = (verdict.get("pursue_blockers_detected") or "").strip()
    existing_quality = verdict.get("existing_match_quality", "OK")
    existing_feedback = (verdict.get("existing_feedback") or "").strip()
    if blockers and existing_quality == "OK" and not existing_feedback:
        props["Match quality"] = to_select("Feedback")
        props["Feedback"] = to_text(_auto_feedback_note(blockers))

    client.update_page(page_id, props)


_AUTO_FEEDBACK_PREFIX = "[Auto]"


def _auto_feedback_note(blockers_csv: str) -> str:
    """Generate the auto-feedback text for a row the scorer wants excluded.

    The `[Auto]` prefix lets the qa agent distinguish scorer-generated signal
    from user-typed feedback. User can override by flipping Match quality back
    to OK — that override is a strong signal to qa that we over-blocked.
    """
    return (
        f"{_AUTO_FEEDBACK_PREFIX} Excluded — detected pursue_blocker(s): "
        f"{blockers_csv}. Override Match quality to OK if this is a false positive."
    )


def _verdict_to_props(v: dict) -> dict:
    """Convert a verdict dict to Notion Tracker row properties.

    Auto-exclude flag: when the scorer detects pursue_blockers, mark
    Match quality = "Feedback" + leave the auto-note. The user can override
    by flipping Match quality back to OK — qa picks up that override on the
    next /fd-recycle-feedback cycle and loosens learned_exclusions.
    """
    vc_source = v.get("vc_source")
    pass_a_verdict = v.get("pass_a_verdict")
    blockers = (v.get("pursue_blockers_detected") or "").strip()
    if blockers:
        match_quality = "Feedback"
        feedback_text = _auto_feedback_note(blockers)
    else:
        match_quality = "OK"
        feedback_text = ""
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
        "Match quality": to_select(match_quality),
        "Feedback":      to_text(feedback_text),
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
