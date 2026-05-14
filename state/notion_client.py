"""Notion API client — the only file in funded-jobs-drop that makes Notion HTTP calls.

All other state/* files use this client. Handles auth, API version pinning,
rate limiting, retries, pagination, and the single-data-source validation lesson
from the parent project's v1.5 incident.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, Optional

# Pin Notion API version — drift caused incidents in parent v1.5
NOTION_API_VERSION = "2025-09-03"
NOTION_BASE_URL = "https://api.notion.com"

# Rate limit: Notion allows ~3 req/sec sustained. Pace ourselves.
MIN_INTERVAL_S = 0.34  # ~3 req/s with small margin

# Retry config for 429 / 5xx / network errors
MAX_RETRIES = 5
INITIAL_BACKOFF_S = 1.0
BACKOFF_MULTIPLIER = 2.0


class NotionError(Exception):
    """Base for Notion-related errors."""


class AuthError(NotionError):
    """Missing or invalid Notion token."""


class SetupError(NotionError):
    """Schema mismatch — column missing, multiple data sources, etc.

    Raised at fire start; user should run /fd-setup --repair.
    """


class RateLimitError(NotionError):
    """Retries exhausted on 429."""


def load_token() -> str:
    """Token precedence: env var → ~/.claude/settings.local.json → raise.

    In dry-run mode (FD_DRY_RUN=1), returns a placeholder so the client
    can instantiate without real auth — callers must avoid making real calls.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return "dry-run-placeholder-token"
    if t := os.environ.get("NOTION_TOKEN"):
        return t
    settings_path = Path.home() / ".claude" / "settings.local.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            t = data.get("funded-drop", {}).get("notion_token")
            if t:
                return t
        except (json.JSONDecodeError, OSError):
            pass
    raise AuthError(
        "Notion token not found. Set NOTION_TOKEN env var, or add "
        "'funded-drop.notion_token' to ~/.claude/settings.local.json."
    )


class NotionClient:
    """Thin Notion API wrapper. The only place in funded-jobs-drop that makes Notion HTTP."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or load_token()
        self._last_request_at: float = 0.0
        self._dry_run = os.environ.get("FD_DRY_RUN") == "1"

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        """Single HTTP request with rate limiting + retries."""
        if self._dry_run:
            raise NotionError(
                "FD_DRY_RUN=1 is set; real Notion calls are blocked. "
                "Tests should mock at the caller level (e.g., state/profile.py), not here."
            )

        url = f"{NOTION_BASE_URL}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }

        backoff = INITIAL_BACKOFF_S
        for _attempt in range(MAX_RETRIES):
            elapsed = time.time() - self._last_request_at
            if elapsed < MIN_INTERVAL_S:
                time.sleep(MIN_INTERVAL_S - elapsed)

            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._last_request_at = time.time()
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                self._last_request_at = time.time()
                if e.code == 429 or 500 <= e.code < 600:
                    retry_after = e.headers.get("Retry-After")
                    sleep_s = float(retry_after) if retry_after else backoff
                    time.sleep(sleep_s)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                error_body = e.read().decode("utf-8", errors="replace")[:500]
                raise NotionError(f"HTTP {e.code} on {method} {path}: {error_body}") from e
            except urllib.error.URLError:
                time.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER

        raise RateLimitError(f"Exhausted {MAX_RETRIES} retries on {method} {path}")

    # ─── Database operations ──────────────────────────────────────────

    def create_database(self, parent_page_id: str, title: str, properties: dict) -> str:
        """Create a database under a parent page. Returns database_id."""
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        resp = self._request("POST", "/v1/databases", body)
        return resp["id"]

    def get_database(self, database_id: str) -> dict:
        """Get database metadata (schema, data_sources, etc.)."""
        return self._request("GET", f"/v1/databases/{database_id}", None)

    def validate_single_data_source(self, database_id: str) -> str:
        """Verify exactly one data_source. Returns its id.

        Raises SetupError on zero or multiple — the v1.5 incident pattern.
        """
        db = self.get_database(database_id)
        data_sources = db.get("data_sources", [])
        if len(data_sources) != 1:
            raise SetupError(
                f"Database {database_id} has {len(data_sources)} data sources "
                f"(expected exactly 1). Delete any stray sources in Notion UI "
                f"and re-run /fd-setup --repair."
            )
        return data_sources[0]["id"]

    def patch_database_properties(self, database_id: str, properties: dict) -> None:
        """Add or modify properties on an existing database. Used for schema repair."""
        self._request("PATCH", f"/v1/databases/{database_id}", {"properties": properties})

    # ─── Data-source / row operations (2025-09-03 API path) ───────────

    def query_data_source(
        self,
        data_source_id: str,
        filter: Optional[dict] = None,
        sorts: Optional[list] = None,
        page_size: int = 100,
    ) -> Iterator[dict]:
        """Query a data source with pagination. Yields each row."""
        start_cursor: Optional[str] = None
        while True:
            body: dict = {"page_size": page_size}
            if filter:
                body["filter"] = filter
            if sorts:
                body["sorts"] = sorts
            if start_cursor:
                body["start_cursor"] = start_cursor

            resp = self._request("POST", f"/v1/data_sources/{data_source_id}/query", body)
            for row in resp.get("results", []):
                yield row
            if not resp.get("has_more"):
                return
            start_cursor = resp.get("next_cursor")

    # ─── Page operations (a "row" in Notion DB is a page) ─────────────

    def create_page(self, parent_database_id: str, properties: dict) -> str:
        """Create a page (row) in a database. Returns page_id."""
        body = {
            "parent": {"database_id": parent_database_id},
            "properties": properties,
        }
        resp = self._request("POST", "/v1/pages", body)
        return resp["id"]

    def update_page(self, page_id: str, properties: dict) -> None:
        """Update a page's properties."""
        body = {"properties": properties}
        self._request("PATCH", f"/v1/pages/{page_id}", body)

    def get_page(self, page_id: str) -> dict:
        """Get a single page by ID."""
        return self._request("GET", f"/v1/pages/{page_id}", None)
