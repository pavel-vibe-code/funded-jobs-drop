"""Favorites source — user-pinned companies fetched via direct ATS adapters.

Each active Favorite row in the Notion Favorites DB gets its job list pulled
from the corresponding native ATS (Greenhouse, Ashby, Lever, etc.) via the
ats_adapters module ported from the parent project.

`fetch_listing` returns full job records — title, location, work_mode, and
(Ashby / Greenhouse / Lever) the complete JD. So a Favorite's jobs are
region-filtered here, before discovery hands them on, and the JD-fetch stage
later skips the per-job HTTP fetch whenever jd_text is already in hand. ATS
types with no rich-listing fetcher degrade to the active-id-only path.

This bypasses VC-portfolio discovery entirely — used for both user-added
companies and (when enabled) the 14 AI-50 supplement entries.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from discovery.prefilter import location_in_variant_region
from discovery.sources.base import DiscoveredJob
from evaluation.ats_adapters import active_ids_for, fetch_listing, parse_workday_url
from state.favorites import read_active
from state.profile import Profile


def _construct_url(ats_type: str, slug: str, job_id: str, careers_url: str = "") -> str:
    """Build the canonical apply URL per ATS type."""
    if ats_type == "workday":
        # job_id is the Workday externalPath (/job/...); compose it onto the
        # tenant's careers host + site to form the real public job URL — which
        # jd_fetch then parses straight back into the CXS detail endpoint.
        parsed = parse_workday_url(careers_url)
        if parsed:
            host, _tenant, site, _ = parsed
            return f"https://{host}/{site}{job_id}"
        return careers_url
    patterns = {
        "greenhouse":      f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}",
        "ashby":           f"https://jobs.ashbyhq.com/{slug}/{job_id}",
        "lever":           f"https://jobs.lever.co/{slug}/{job_id}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
        "workable":        f"https://apply.workable.com/{slug}/j/{job_id}",
        "recruitee":       f"https://{slug}.recruitee.com/o/{job_id}",
        "personio":        f"https://{slug}.jobs.personio.de/job/{job_id}",
        "bamboohr":        f"https://{slug}.bamboohr.com/careers/{job_id}",
        "teamtailor":      f"https://{slug}.teamtailor.com/jobs/{job_id}",
        "homerun":         f"https://api.homerun.co/v1/jobs/{job_id}",
        "comeet":          f"https://www.comeet.co/jobs/{slug}/{job_id}",
    }
    return patterns.get(ats_type, f"https://example.com/unknown/{slug}/{job_id}")


def _convert(record: dict, favorite_name: str, favorite_slug: str,
             ats_type: str, careers_url: str) -> DiscoveredJob:
    """Build a DiscoveredJob from a fetch_listing record.

    Record keys: source_job_id (required), title, location, work_mode, jd_text.
    jd_text is the full JD when the ATS listing carried it (Ashby / Greenhouse /
    Lever), else None — the JD-fetch stage fetches it per-job in that case.
    A sparse record (only source_job_id, from the active-id fallback) is fine:
    every other field defaults.

    work_mode falls back to "unknown" — NOT "on_site" — when the listing gave
    no signal: the prefilter must not drop an unknown-mode job on the
    relocation rule (it could be remote); Pass B reads the JD and judges.
    """
    canonical_url = _construct_url(
        ats_type, favorite_slug, record["source_job_id"], careers_url)
    location = record.get("location") or ""
    return DiscoveredJob(
        canonical_url=canonical_url,
        title=record.get("title") or "",
        company_name=favorite_name,
        company_slug=favorite_slug,
        raw_location=[location] if location else [],
        work_mode=record.get("work_mode") or "unknown",
        posted_at=datetime.now(timezone.utc),  # ATS listings rarely date-stamp reliably
        source_platform="Favorites",
        raw={},
        relevance_prior=0.5,
        region="OTHER",  # variant region filter applies below + at later stages
        vc_source=None,
        source_job_id=record["source_job_id"],
        jd_text=record.get("jd_text") or None,
    )


def fetch(profile: Profile,
          since_epoch: int) -> tuple[list[DiscoveredJob], list[str], int]:
    """Fetch active jobs for every active Favorite via its ATS adapter.

    Returns (jobs, per-favorite error strings, region_dropped_count). Errors
    propagate to the Runs DB's errors_summary so silently-broken Favorite slugs
    are visible; region_dropped_count lands in discovery-metrics → jsonl_log.

    Jobs whose listing location is detectably outside the profile's variant
    region are dropped here — before discovery hands them on. Ambiguous
    locations (no country detected) are kept and deferred to post-JD screening.

    `since_epoch` is unused — favorites adapters return all active jobs; the
    prefilter / evaluation steps handle recency.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return [], [], 0

    jobs: list[DiscoveredJob] = []
    errors: list[str] = []
    region_dropped = 0
    for fav in read_active():
        if not fav.ats_type:
            continue
        try:
            records, err = fetch_listing(
                fav.ats_type, slug=fav.ats_slug, careers_url=fav.careers_url)
            if records is None and err is None:
                # ATS has no rich-listing fetcher — degrade to active IDs only.
                ids, err = active_ids_for(
                    fav.ats_type, fav.ats_slug, careers_url=fav.careers_url)
                records = [{"source_job_id": i} for i in (ids or ())]
            if err:
                msg = f"Favorites/{fav.name} ({fav.ats_type}:{fav.ats_slug}): {err}"
                print(f"  [{msg}]")
                errors.append(msg)
                continue
            kept = 0
            for rec in (records or []):
                # Pre-discovery region filter (generalizes the v0.1.17 Workday
                # path to every ATS): drop a job whose listing location is
                # detectably out of region before it travels any further.
                if location_in_variant_region(rec.get("location") or "", profile) is False:
                    region_dropped += 1
                    continue
                jobs.append(_convert(rec, fav.name, fav.ats_slug,
                                     fav.ats_type, fav.careers_url))
                kept += 1
            print(f"  [Favorites/{fav.name}: {fav.ats_type} — "
                  f"{len(records or [])} listed, {kept} kept after region filter]")
        except Exception as e:
            msg = f"Favorites/{fav.name}: {type(e).__name__}: {e}"
            print(f"  [{msg}]")
            errors.append(msg)
    return jobs, errors, region_dropped
