"""Job description fetcher — source-aware.

Getro JDs: fetch the per-job detail page (no auth, no LLM), parse __NEXT_DATA__
  for currentJob.description.
Consider/Favorites JDs: follow the canonical URL to the native ATS (Greenhouse,
  Ashby, Lever) → fall back to the customer's page HTML stripped to main
  content when the URL doesn't match a known ATS pattern.

Page-scrape fallback covers:
  - Greenhouse-via-custom-domain (wiz.io, bolt.eu, bigid.com etc. that carry
    `gh_jid=` but render the JD inline)
  - TeamTailor / Comeet / Recruitee-via-custom-domain
  - Any other custom careers page that ships the JD in static HTML

Hard-anti-scrape sites (Cloudflare bot block, Revolut, LinkedIn) will still
fail; those rows land in Tracker as jd_fetch_failed for manual review.

Returns (jd_text, error_msg).
"""
from __future__ import annotations

import html as html_lib
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

# Minimum scraped-text length to accept as a JD. Below this and the extraction
# almost certainly missed real content — we'd rather fail than send the scorer
# a noisy 50-char string.
MIN_SCRAPED_JD_CHARS = 400


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
        ats_text, ats_err = fetch_jd_for_url(job.canonical_url)
        if ats_text:
            return ats_text, None
        return "", f"Getro detail failed ({err}); ATS fallback failed ({ats_err})"

    # Consider or Favorites: canonical URL goes to native ATS
    return fetch_jd_for_url(job.canonical_url)


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

def fetch_jd_for_url(url: str) -> tuple[str, Optional[str]]:
    """Detect ATS from URL and fetch JD via the appropriate endpoint.

    Direct ATS paths: Greenhouse, Ashby, Lever.
    Fallback: any other URL goes to the page-scrape extractor.

    Used by both the in-fire JD fetch (via `fetch()`) and the rescore path.
    """
    if "boards-api.greenhouse.io" in url or "boards.greenhouse.io" in url or "job-boards.greenhouse.io" in url:
        return _fetch_greenhouse_jd(url)
    if "jobs.ashbyhq.com" in url:
        return _fetch_ashby_jd(url)
    if "jobs.lever.co" in url:
        return _fetch_lever_jd(url)
    # Generic page-scrape fallback. Handles Greenhouse-on-custom-domain
    # (gh_jid=… URLs on wiz.io, bolt.eu, bigid.com, etc.), TeamTailor and
    # other custom careers domains. Sites with aggressive bot protection
    # (Cloudflare, LinkedIn) will still fail here — that's acceptable.
    return _fetch_via_page_scrape(url)


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


_ashby_board_cache: dict[str, dict] = {}


def _fetch_ashby_jd(url: str) -> tuple[str, Optional[str]]:
    """Fetch via Ashby's board list endpoint, then filter by job_id.

    The per-job endpoint `/posting-api/job-board/{slug}/{job_id}` returns 401
    for many boards (some private-flag interaction we can't influence). The
    list endpoint `/posting-api/job-board/{slug}` returns every job with full
    `descriptionHtml`, no auth, and is cached per-slug for this fire.
    """
    m = re.search(r"jobs\.ashbyhq\.com/([\w.-]+)/([a-f0-9-]+)", url)
    if not m:
        return "", f"could not parse Ashby slug+id from {url}"
    slug, job_id = m.group(1), m.group(2)

    if slug not in _ashby_board_cache:
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        body, err = http_get(api_url, accept="application/json")
        if err or not body:
            return "", f"Ashby list API error: {err}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            return "", f"Ashby JSON parse: {e}"
        _ashby_board_cache[slug] = {j["id"]: j for j in data.get("jobs", []) if j.get("id")}

    job = _ashby_board_cache[slug].get(job_id)
    if not job:
        return "", f"Ashby job {job_id} not in {slug} board (likely delisted)"
    description = job.get("descriptionHtml") or job.get("descriptionPlain") or ""
    if not description:
        return "", "Ashby description fields empty"
    return description, None


# ─── Generic page-scrape fallback ─────────────────────────────────────

# Block elements stripped before text extraction. These almost always carry
# site chrome (nav, footer, sign-up forms) — keeping them turns a 4 KB JD
# into 40 KB of noise that confuses the scorer.
_STRIP_TAGS = re.compile(
    r"<(script|style|noscript|nav|header|footer|aside|svg|iframe|form)\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _strip_html_to_text(html: str) -> str:
    """Convert raw HTML to plain text. Deterministic, no LLM.

    1. Drop boilerplate blocks (script/style/nav/header/footer/aside/form).
    2. Replace line-breaking tags with newlines so the layout stays readable.
    3. Drop remaining tags.
    4. Decode entities, collapse whitespace.
    """
    cleaned = _STRIP_TAGS.sub("", html)
    # Replace breaking tags with newlines so paragraph structure survives.
    cleaned = re.sub(r"</(p|div|li|h[1-6]|tr|br)\s*/?>", "\n", cleaned,
                     flags=re.IGNORECASE)
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = _TAG.sub("", cleaned)
    cleaned = html_lib.unescape(cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def _slug_from_host(url: str) -> Optional[str]:
    """Derive a likely Greenhouse board slug from a URL host.

    Heuristic for the gh_jid-on-custom-domain case: bigid.com → 'bigid',
    careers.bigid.com → 'bigid', www.wiz.io → 'wiz'. Not always right
    (wiz.io's actual GH slug differs), but cheap to try as a fallback.
    """
    m = re.search(r"https?://([^/]+)", url)
    if not m:
        return None
    host = m.group(1).lower()
    parts = [p for p in host.split(".") if p not in ("www", "careers", "jobs")]
    # Drop the TLD (last) and take what's left of the leftmost meaningful segment.
    if len(parts) >= 2:
        return parts[0]
    return None


def _try_greenhouse_via_gh_jid(url: str) -> tuple[str, Optional[str]]:
    """For URLs with ?gh_jid=<id> on a custom domain, try the Greenhouse API.

    Slug is guessed from the host. Returns ("", err) on any failure so the
    caller can fall through to the scrape result.
    """
    m = re.search(r"[?&]gh_jid=(\d+)", url)
    if not m:
        return "", "no gh_jid in URL"
    job_id = m.group(1)
    slug = _slug_from_host(url)
    if not slug:
        return "", "could not derive slug from host"
    return _fetch_greenhouse_jd(
        f"https://boards.greenhouse.io/{slug}/jobs/{job_id}"
    )


def _fetch_via_page_scrape(url: str) -> tuple[str, Optional[str]]:
    """Fetch the page HTML and strip to main text. Last-resort JD source.

    If the URL has a gh_jid query param and the scrape returns too little
    content (SPA shell), retry via Greenhouse API with slug-from-host.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
    )
    html = None
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        scrape_err = f"page-scrape HTTP {e.code} for {url}"
    except urllib.error.URLError as e:
        scrape_err = f"page-scrape URL error for {url}: {e.reason}"
    else:
        text = _strip_html_to_text(html)
        if len(text) >= MIN_SCRAPED_JD_CHARS:
            return text, None
        scrape_err = (
            f"page-scrape extracted only {len(text)} chars (min {MIN_SCRAPED_JD_CHARS}) "
            f"from {url}; likely SPA shell"
        )

    # Scrape didn't give us enough. If the URL has gh_jid, try Greenhouse API
    # with slug-from-host as a last resort.
    if "gh_jid=" in url:
        gh_text, gh_err = _try_greenhouse_via_gh_jid(url)
        if gh_text:
            return gh_text, None
        return "", f"{scrape_err}; gh_jid fallback: {gh_err}"

    return "", scrape_err


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
