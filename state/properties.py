"""Notion property converters.

extract_*: pull Python values from Notion API responses
to_*: format Python values into Notion property update payloads

Used by state/profile.py, favorites.py, tracker.py, runs.py.
"""
from __future__ import annotations

from typing import Optional


# ─── Extractors (Notion property → Python value) ────────────────────────

def extract_title(prop: Optional[dict]) -> str:
    if not prop or prop.get("type") != "title":
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))


def extract_text(prop: Optional[dict]) -> str:
    """For rich_text or title properties — extracts concatenated plain text."""
    if not prop:
        return ""
    if prop.get("type") == "title":
        return extract_title(prop)
    if prop.get("type") == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    return ""


def extract_select(prop: Optional[dict]) -> Optional[str]:
    if not prop or prop.get("type") != "select":
        return None
    sel = prop.get("select")
    return sel.get("name") if sel else None


def extract_multi_select(prop: Optional[dict]) -> list[str]:
    if not prop or prop.get("type") != "multi_select":
        return []
    return [s.get("name", "") for s in prop.get("multi_select", [])]


def extract_checkbox(prop: Optional[dict]) -> bool:
    if not prop or prop.get("type") != "checkbox":
        return False
    return bool(prop.get("checkbox", False))


def extract_number(prop: Optional[dict]) -> Optional[float]:
    if not prop or prop.get("type") != "number":
        return None
    return prop.get("number")


def extract_date(prop: Optional[dict]) -> Optional[str]:
    """Returns ISO date string or None."""
    if not prop or prop.get("type") != "date":
        return None
    d = prop.get("date")
    return d.get("start") if d else None


def extract_url(prop: Optional[dict]) -> Optional[str]:
    if not prop or prop.get("type") != "url":
        return None
    return prop.get("url")


# ─── Formatters (Python value → Notion property update payload) ─────────

def to_title(value: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": value or ""}}]}


def to_text(value: str) -> dict:
    """For rich_text properties."""
    return {"rich_text": [{"type": "text", "text": {"content": value or ""}}]}


_NOTION_RICH_TEXT_CHUNK = 1900  # Notion caps individual rich_text chunks at 2000


def to_text_chunked(value: str) -> dict:
    """For rich_text properties carrying long content (e.g. jsonl_log).

    Notion rejects any single rich_text content > 2000 chars. Splitting
    into ≤1900-char chunks lets us pack tens of kB of debug log into one
    property cleanly.
    """
    if not value:
        return {"rich_text": []}
    chunks = [
        value[i:i + _NOTION_RICH_TEXT_CHUNK]
        for i in range(0, len(value), _NOTION_RICH_TEXT_CHUNK)
    ]
    return {"rich_text": [
        {"type": "text", "text": {"content": c}} for c in chunks
    ]}


def to_select(value: Optional[str]) -> dict:
    if not value:
        return {"select": None}
    return {"select": {"name": value}}


def to_multi_select(values: Optional[list[str]]) -> dict:
    return {"multi_select": [{"name": v} for v in (values or [])]}


def to_checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


def to_number(value: Optional[float]) -> dict:
    return {"number": value}


def to_date(value: Optional[str]) -> dict:
    if not value:
        return {"date": None}
    return {"date": {"start": value}}


def to_url(value: Optional[str]) -> dict:
    if not value:
        return {"url": None}
    return {"url": value}
