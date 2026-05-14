"""Consider source — 7 VCs via /api-boards/search-jobs.

POST to https://jobs.{vc}.com/api-boards/search-jobs with cursor pagination.
Auth: same-origin CSRF token bootstrapped from the SPA HTML.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from typing import Optional

from config.vcs import CONSIDER_VCS
from discovery.sources.base import DiscoveredJob, normalize_seniority, to_yearly
from state.profile import Profile


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

# UK + Ireland tokens, used to exclude when profile.eu_include_uk_ie is False
_UK_IE_EXCLUDE_TOKENS = {
    "united kingdom", "uk", "england", "scotland", "wales",
    "northern ireland", "ireland",
    "london", "manchester", "edinburgh", "glasgow", "leeds",
    "bristol", "belfast", "dublin", "cork", "galway",
}


def _bootstrap(host: str) -> tuple[CookieJar, str, str]:
    """GET the SPA HTML, extract session cookie + CSRF + board_id."""
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        f"https://{host}/jobs",
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with opener.open(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    csrf_m = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
    if not csrf_m:
        raise RuntimeError(f"csrfToken not found in {host}/jobs HTML")

    sid_m = re.search(r"window\.serverInitialData\s*=\s*({.+?});", html, re.DOTALL)
    if not sid_m:
        raise RuntimeError(f"serverInitialData not found in {host}/jobs HTML")
    board_id = json.loads(sid_m.group(1)).get("fixedBoard", "")
    if not board_id:
        raise RuntimeError(f"fixedBoard missing in serverInitialData for {host}")

    return cj, csrf_m.group(1), board_id


def _variant_to_locations(variant: str) -> list[str]:
    """Map Profile.variant to Consider's locations query."""
    if variant == "EU":
        return ["Europe"]
    if variant == "US":
        return ["United States", "Canada"]
    return []


def _is_uk_or_ireland(raw: dict) -> bool:
    """Detect UK/Ireland-tagged jobs for client-side exclusion in EU variant."""
    for nl in raw.get("normalizedLocations") or []:
        if (nl.get("id") or "").lower() in _UK_IE_EXCLUDE_TOKENS:
            return True
    for loc in raw.get("locations") or []:
        low = loc.lower()
        if any(tok in low for tok in _UK_IE_EXCLUDE_TOKENS):
            return True
    return False


def _infer_work_mode(raw: dict) -> str:
    """Consider exposes remote + hybrid as separate booleans; infer canonical mode."""
    remote = bool(raw.get("remote"))
    hybrid = bool(raw.get("hybrid"))
    if hybrid:
        return "hybrid"
    if remote:
        return "remote"
    return "on_site"


def _safe_label_list(items: Optional[list], key: str = "label") -> list[str]:
    """Extract a list of labels from a list of dicts; tolerate malformed entries."""
    return [
        it[key] for it in (items or [])
        if isinstance(it, dict) and isinstance(it.get(key), str)
    ]


def _convert(raw: dict, vc_name: str, variant: str) -> DiscoveredJob:
    """Convert one Consider job dict to DiscoveredJob."""
    posted_iso = raw.get("timeStamp", "")
    try:
        posted_at = datetime.fromisoformat(posted_iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        posted_at = datetime.now(timezone.utc)

    salary = raw.get("salary") or {}
    period_obj = salary.get("period")
    period = period_obj.get("value") if isinstance(period_obj, dict) else None
    currency_obj = salary.get("currency")
    currency = currency_obj.get("value") if isinstance(currency_obj, dict) else None

    seniority_ids = raw.get("jobSeniorityIds") or []
    seniority = normalize_seniority(seniority_ids[0] if seniority_ids else None)

    scores = raw.get("scores") or {}
    relevance = min(1.0, max(0.0, float(scores.get("score", 500)) / 1000))

    return DiscoveredJob(
        canonical_url=raw.get("applyUrl", ""),
        title=raw.get("title", ""),
        company_name=raw.get("companyName", ""),
        company_slug=raw.get("companySlug"),
        company_domain=raw.get("companyDomain"),
        company_size=raw.get("companyStaffCount"),
        company_size_is_bucket=False,
        raw_location=list(raw.get("locations") or []),
        normalized_locations=[
            nl["id"] for nl in (raw.get("normalizedLocations") or [])
            if isinstance(nl, dict) and isinstance(nl.get("id"), str)
        ],
        work_mode=_infer_work_mode(raw),
        posted_at=posted_at,
        skills=_safe_label_list(raw.get("skills")),
        required_skills=_safe_label_list(raw.get("requiredSkills")),
        preferred_skills=_safe_label_list(raw.get("preferredSkills")),
        seniority=seniority,
        departments=list(raw.get("departments") or []),
        job_functions=_safe_label_list(raw.get("jobFunctions")),
        industry_tags=_safe_label_list(raw.get("markets")),
        stage=(raw.get("fundingLV") or {}).get("label") if isinstance(raw.get("fundingLV"), dict) else None,
        salary_min_yearly=to_yearly(salary.get("minValue"), period),
        salary_max_yearly=to_yearly(salary.get("maxValue"), period),
        salary_currency=currency,
        salary_disclosed=salary.get("minValue") is not None,
        min_years_exp=raw.get("minYearsExp"),
        manager=raw.get("manager") if "manager" in raw else None,
        region=variant,  # variant doubles as region tag (EU / US)
        vc_source=vc_name,
        relevance_prior=relevance,
        source_platform="Consider",
        source_job_id=str(raw.get("jobId", "")),
        raw=raw,
    )


def _fetch_one_vc(vc_name: str, host: str, fallback_board_id: str,
                  since_epoch: int, profile: Profile) -> list[DiscoveredJob]:
    """Fetch jobs from one Consider VC. Paginates via meta.sequence cursor."""
    cj, csrf, board_id = _bootstrap(host)
    if not board_id:
        board_id = fallback_board_id  # tolerate empty serverInitialData

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    age_days = max(1, int((time.time() - since_epoch) / 86400))
    posted_since = f"P{age_days}D"
    locations = _variant_to_locations(profile.variant)
    excluded_uk_ie = profile.variant == "EU" and not profile.eu_include_uk_ie

    jobs: list[DiscoveredJob] = []
    cursor: Optional[str] = None

    for _page in range(15):  # safety bound
        meta: dict = {"size": 200}
        if cursor:
            meta["sequence"] = cursor
        query = {
            "locations": locations,
            "postedSince": posted_since,
            "promoteFeatured": True,
        }
        payload = {"meta": meta, "board": {"id": board_id, "isParent": True}, "query": query}

        req = urllib.request.Request(
            f"https://{host}/api-boards/search-jobs",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-csrf-token": csrf,
                "Origin": f"https://{host}",
                "Referer": f"https://{host}/jobs",
            },
        )
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read())

        for raw in data.get("jobs", []):
            if excluded_uk_ie and _is_uk_or_ireland(raw):
                continue
            jobs.append(_convert(raw, vc_name, profile.variant))

        cursor = data.get("meta", {}).get("sequence")
        if not cursor:
            break
        time.sleep(0.2)  # polite pacing between pages

    return jobs


def fetch(profile: Profile, since_epoch: int) -> list[DiscoveredJob]:
    """Fetch from all 7 Consider VCs since since_epoch.

    Individual VC failures are logged and don't abort the whole fetch.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return []  # fixtures handled at higher level

    all_jobs: list[DiscoveredJob] = []
    for vc in CONSIDER_VCS:
        try:
            jobs = _fetch_one_vc(
                vc["name"], vc["subdomain"], vc["board_id"], since_epoch, profile
            )
            all_jobs.extend(jobs)
        except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as e:
            print(f"[Consider/{vc['name']}] fetch error: {type(e).__name__}: {e}")
    return all_jobs
