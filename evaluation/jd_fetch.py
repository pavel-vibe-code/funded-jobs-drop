"""Job description fetcher — source-aware.

Getro JDs: fetch the per-job detail page (no auth, no LLM), parse __NEXT_DATA__
  for currentJob.description.
Consider/Favorites JDs: follow the canonical URL to the native ATS and fetch
  the JD via per-ATS endpoints (Greenhouse, Ashby, Lever supported in v0.1.0).
Fallback: Getro URLs whose detail page fails AND map to a known ATS retry via
  the ATS path before giving up.

Returns (jd_text, error_msg). Empty jd_text + error_msg means failure → row
goes to jd_fetch_failed in Tracker for manual review.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from config.vcs import GETRO_VCS
from discovery.sources.base import DiscoveredJob
from evaluation.ats_adapters import http_get


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def fetch(job: DiscoveredJob) -> tuple[str, Optional[str]]:
    """Fetch JD text/HTML for a job. Returns (jd_text, error_msg).

    error_msg is None on success, populated string on failure.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return "DRY_RUN_PLACEHOLDER_JD", None

    if job.source_platform == "Getro":
        text, err = _fetch_getro_detail(job)
        if text:
            return text, None
        # Fallback: try ATS path if URL maps to known ATS
        ats_text, ats_err = _try_ats_url(job.canonical_url)
        if ats_text:
            return ats_text, None
        return "", f"Getro detail failed ({err}); ATS fallback failed ({ats_err})"

    # Consider or Favorites: canonical URL goes to native ATS
    return _try_ats_url(job.canonical_url)


def _fetch_getro_detail(job: DiscoveredJob) -> tuple[str, Optional[str]]:
    """Fetch Getro per-job detail page, extract description from __NEXT_DATA__."""
    raw = job.raw or {}
    org = raw.get("organization") or {}
    job_slug = raw.get("slug")
    company_slug = org.get("slug")
    if not job_slug or not company_slug:
        return "", "missing slug fields in raw Getro response"

    host = next(
        (vc["subdomain"] for vc in GETRO_VCS if vc["name"] == job.vc_source),
        None,
    )
    if not host:
        return "", f"unknown VC for Getro detail: vc_source={job.vc_source!r}"

    url = f"https://{host}/companies/{company_slug}/jobs/{job_slug}"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return "", f"Getro detail HTTP error: {e}"

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return "", "__NEXT_DATA__ block missing in Getro detail page"
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return "", f"__NEXT_DATA__ JSON malformed: {e}"

    current = (
        data.get("props", {})
            .get("pageProps", {})
            .get("initialState", {})
            .get("jobs", {})
            .get("currentJob", {})
    )
    description = current.get("description") or ""
    if not description:
        return "", "currentJob.description empty"
    return description, None


# ─── ATS-specific JD fetchers ─────────────────────────────────────────

def _try_ats_url(url: str) -> tuple[str, Optional[str]]:
    """Detect ATS from URL and fetch JD via the appropriate endpoint.

    Supported in v0.1.0: Greenhouse, Ashby, Lever. Others → unsupported.
    """
    if "boards-api.greenhouse.io" in url or "boards.greenhouse.io" in url or "job-boards.greenhouse.io" in url:
        return _fetch_greenhouse_jd(url)
    if "jobs.ashbyhq.com" in url:
        return _fetch_ashby_jd(url)
    if "jobs.lever.co" in url:
        return _fetch_lever_jd(url)
    return "", f"JD fetch not supported for URL: {url}"


def _fetch_greenhouse_jd(url: str) -> tuple[str, Optional[str]]:
    """GET boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true."""
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?|v1/boards/)?([\w-]+)/jobs?/(\d+)", url)
    if not m:
        m = re.search(r"job-boards\.greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    if not m:
        m = re.search(r"boards\.greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    if not m:
        return "", f"could not parse Greenhouse slug+id from {url}"

    slug, job_id = m.group(1), m.group(2)
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true"
    body, err = http_get(api_url, accept="application/json")
    if err or not body:
        return "", f"Greenhouse API error: {err}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return "", f"Greenhouse JSON parse: {e}"
    content = data.get("content", "")
    if not content:
        return "", "Greenhouse content field empty"
    return content, None


def _fetch_ashby_jd(url: str) -> tuple[str, Optional[str]]:
    """GET api.ashbyhq.com/posting-api/job-board/{slug}/{job_id}."""
    m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([a-f0-9-]+)", url)
    if not m:
        return "", f"could not parse Ashby slug+id from {url}"
    slug, job_id = m.group(1), m.group(2)
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}/{job_id}"
    body, err = http_get(api_url, accept="application/json")
    if err or not body:
        return "", f"Ashby API error: {err}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return "", f"Ashby JSON parse: {e}"
    description = data.get("descriptionHtml") or data.get("description") or ""
    if not description:
        return "", "Ashby description fields empty"
    return description, None


def _fetch_lever_jd(url: str) -> tuple[str, Optional[str]]:
    """GET api.lever.co/v0/postings/{slug}/{job_id}?mode=json."""
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([a-f0-9-]+)", url)
    if not m:
        return "", f"could not parse Lever slug+id from {url}"
    slug, job_id = m.group(1), m.group(2)
    api_url = f"https://api.lever.co/v0/postings/{slug}/{job_id}?mode=json"
    body, err = http_get(api_url, accept="application/json")
    if err or not body:
        return "", f"Lever API error: {err}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return "", f"Lever JSON parse: {e}"
    description = data.get("descriptionPlain") or data.get("description") or ""
    if not description:
        return "", "Lever description empty"
    return description, None
