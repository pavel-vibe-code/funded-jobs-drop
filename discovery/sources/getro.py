"""Getro source — 5 VCs via /api/v2/collections/{network_id}/search/jobs.

POST endpoint, no auth required beyond a matching Origin header.
Server-side filtering: only `query` (text search) is honored; locations/dates
are silently ignored. So we walk pages by recency and stop when the oldest
job in a batch is older than the cutoff (client-side time filter).
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from config.vcs import GETRO_VCS
from discovery.sources.base import DiscoveredJob, normalize_seniority, to_yearly
from state.profile import Profile


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


# Per-variant location filters for Getro (client-side since server-side ignored).
# A job must match at least one include keyword in its searchable_locations
# AND not match any exclude keyword.
VARIANT_LOCATION_FILTERS = {
    "EU": {
        "include": {
            "europe", "european union", "emea", "western europe",
            "germany", "france", "spain", "netherlands", "italy",
            "poland", "portugal", "sweden", "denmark", "finland",
            "norway", "austria", "switzerland", "belgium", "czech",
            "czechia", "lithuania", "estonia", "latvia", "romania",
            "bulgaria", "hungary", "slovakia", "slovenia", "croatia",
            "greece",
            "berlin", "munich", "paris", "amsterdam", "stockholm",
            "copenhagen", "madrid", "barcelona", "lisbon", "warsaw",
            "krakow", "prague", "vienna", "zurich", "rome", "milan",
            "helsinki", "oslo", "vilnius", "riga", "tallinn",
        },
        "exclude_when_no_eu_uk_ie": {
            "united kingdom", " uk", "england", "scotland", "wales",
            "northern ireland", "ireland",
            "london", "manchester", "birmingham", "edinburgh", "glasgow",
            "leeds", "bristol", "belfast", "dublin", "cork", "galway",
        },
    },
    "US": {
        "include": {
            "united states", "usa", "canada",
            "new york", "san francisco", "boston", "austin", "seattle",
            "los angeles", "chicago", "denver", "atlanta", "washington",
            "miami", "philadelphia", "san diego",
            "toronto", "vancouver", "montreal",
            "california", "texas", "florida", "massachusetts",
        },
        "exclude_when_no_eu_uk_ie": set(),  # not applicable for US variant
    },
}


def _matches_variant(raw: dict, profile: Profile) -> bool:
    """Apply variant location filter client-side (Getro doesn't filter server-side)."""
    cfg = VARIANT_LOCATION_FILTERS.get(profile.variant)
    if not cfg:
        return False
    locs = " ".join(raw.get("searchable_locations") or []).lower()
    # Exclude check first
    if profile.variant == "EU" and not profile.eu_include_uk_ie:
        if any(tok in locs for tok in cfg["exclude_when_no_eu_uk_ie"]):
            return False
    # Include check: must have at least one variant-region signal
    return any(tok in locs for tok in cfg["include"])


def _convert(raw: dict, vc_name: str, variant: str) -> DiscoveredJob:
    """Convert one Getro job dict to DiscoveredJob."""
    created_at = raw.get("created_at") or 0
    try:
        posted_at = datetime.fromtimestamp(int(created_at), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        posted_at = datetime.now(timezone.utc)

    organization = raw.get("organization") or {}

    work_mode = (raw.get("work_mode") or "on_site").lower()
    if work_mode not in ("remote", "hybrid", "on_site"):
        work_mode = "on_site"

    # Getro salary is in cents
    min_cents = raw.get("compensation_amount_min_cents")
    max_cents = raw.get("compensation_amount_max_cents")
    period = raw.get("compensation_period")
    sal_min = to_yearly(min_cents / 100, period) if min_cents else None
    sal_max = to_yearly(max_cents / 100, period) if max_cents else None

    seniority = normalize_seniority(raw.get("seniority"))

    return DiscoveredJob(
        canonical_url=raw.get("url", ""),
        title=raw.get("title", ""),
        company_name=organization.get("name", ""),
        company_slug=organization.get("slug"),
        company_size=organization.get("head_count"),
        company_size_is_bucket=True,  # Getro head_count is a bucket index, not a count
        raw_location=list(raw.get("searchable_locations") or []),
        normalized_locations=[
            ld.get("name") for ld in (raw.get("location_details") or [])
            if isinstance(ld, dict) and ld.get("name")
        ],
        work_mode=work_mode,
        posted_at=posted_at,
        skills=[s for s in (raw.get("skills") or []) if isinstance(s, str)],
        seniority=seniority,
        industry_tags=list(organization.get("industry_tags") or []),
        stage=organization.get("stage"),
        salary_min_yearly=sal_min,
        salary_max_yearly=sal_max,
        salary_currency=raw.get("compensation_currency"),
        salary_disclosed=min_cents is not None,
        offers_equity=raw.get("compensation_offers_equity"),
        region=variant,
        vc_source=vc_name,
        relevance_prior=0.5,  # Getro doesn't expose a quality score
        source_platform="Getro",
        source_job_id=str(raw.get("id", "")),
        raw=raw,
    )


def _fetch_one_vc(vc_name: str, host: str, network_id: int,
                  since_epoch: int, profile: Profile,
                  max_pages: int = 30) -> list[DiscoveredJob]:
    """Walk pages by recency; stop when the oldest in batch is older than cutoff."""
    jobs: list[DiscoveredJob] = []

    for page in range(max_pages):
        payload = {"hits_per_page": 200, "page": page, "query": ""}
        req = urllib.request.Request(
            f"https://api.getro.com/api/v2/collections/{network_id}/search/jobs",
            method="POST",
            data=json.dumps(payload).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": f"https://{host}",
                "Referer": f"https://{host}/jobs",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        batch = (data.get("results") or {}).get("jobs", [])
        if not batch:
            break

        kept_in_batch = 0
        for raw in batch:
            created_at = raw.get("created_at") or 0
            if created_at < since_epoch:
                continue
            if not _matches_variant(raw, profile):
                continue
            jobs.append(_convert(raw, vc_name, profile.variant))
            kept_in_batch += 1

        # Stop if every job in this batch is older than the cutoff
        oldest = min((r.get("created_at") or 0) for r in batch)
        if oldest < since_epoch and kept_in_batch == 0:
            break

        time.sleep(0.2)

    return jobs


def fetch(profile: Profile, since_epoch: int) -> tuple[list[DiscoveredJob], list[str]]:
    """Fetch from all 5 Getro VCs. Returns (jobs, per-VC error strings).

    Per-VC errors surface in the orchestrator's errors_summary so Cloud Routine
    fires don't silently swallow upstream-block / egress-misconfig failures.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return [], []

    all_jobs: list[DiscoveredJob] = []
    errors: list[str] = []
    for vc in GETRO_VCS:
        try:
            jobs = _fetch_one_vc(
                vc["name"], vc["subdomain"], vc["network_id"], since_epoch, profile
            )
            all_jobs.extend(jobs)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
            err = f"Getro/{vc['name']}: {type(e).__name__}: {e}"
            print(f"  [{err}]")
            errors.append(err)
    return all_jobs, errors
