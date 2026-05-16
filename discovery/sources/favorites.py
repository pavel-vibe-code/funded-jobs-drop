"""Favorites source — user-pinned companies fetched via direct ATS adapters.

Each active Favorite row in the Notion Favorites DB gets its job list pulled
from the corresponding native ATS (Greenhouse, Ashby, Lever, etc.) using the
existing ats_adapters module ported from the parent project.

This bypasses the VC-portfolio discovery entirely — used for both user-added
companies and (when enabled) the 14 AI-50 supplement entries.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from discovery.prefilter import location_in_variant_region
from discovery.sources.base import DiscoveredJob
from evaluation.ats_adapters import (
    active_ids_for, fetch_workday_postings, parse_workday_url,
)
from state.favorites import read_active
from state.profile import Profile


def _convert(active_id: str, favorite_name: str, favorite_slug: str,
             ats_type: str, careers_url: str) -> DiscoveredJob:
    """Build a sparse DiscoveredJob from an ATS active-jobs response.

    The ats_adapters module exposes active_ids_for which returns just the
    set of currently-listed job IDs. We don't get the full JD or rich fields
    at Discovery time — that comes during Evaluation's JD fetch step.
    """
    # Construct a canonical URL pattern per ATS type
    canonical_url = _construct_url(ats_type, favorite_slug, active_id, careers_url)
    return DiscoveredJob(
        canonical_url=canonical_url,
        title="",  # populated at Evaluation JD-fetch time
        company_name=favorite_name,
        company_slug=favorite_slug,
        raw_location=[],
        work_mode="on_site",  # default; will be refined when JD is fetched
        posted_at=datetime.now(timezone.utc),  # unknown until JD fetch
        source_platform="Favorites",
        raw={"active_id": active_id, "ats_type": ats_type, "careers_url": careers_url},
        relevance_prior=0.5,
        region="OTHER",  # variant filter applies at later stage
        vc_source=None,
        source_job_id=active_id,
    )


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


def _workday_jobs(fav, profile: Profile) -> tuple[list[DiscoveredJob], Optional[str]]:
    """Workday Favorite → DiscoveredJobs, region-filtered before the JD fetch.

    Workday's CXS list response carries each posting's location, so a job
    whose location is detectably outside the profile's variant region is
    dropped here — saving the per-job JD fetch (Workday mega-tenants like
    MSD/Nvidia/Adobe are mostly out-of-region for an EU/US-scoped profile).
    Ambiguous locations (no country detected) are kept and deferred to
    post-JD screening, exactly like every other Favorite.
    """
    postings, err = fetch_workday_postings(fav.careers_url)
    if err:
        return [], err
    jobs: list[DiscoveredJob] = []
    for p in postings:
        if location_in_variant_region(p["location"], profile) is False:
            continue  # detected out-of-region — skip before the JD fetch
        jobs.append(_convert(p["external_path"], fav.name, fav.ats_slug,
                             "workday", fav.careers_url))
    print(f"  [Favorites/{fav.name}: workday — {len(postings)} postings, "
          f"{len(jobs)} kept after pre-JD region filter]")
    return jobs, None


def fetch(profile: Profile, since_epoch: int) -> tuple[list[DiscoveredJob], list[str]]:
    """Fetch active jobs for every active Favorite via its ATS adapter.

    Returns (jobs, per-favorite error strings). Errors propagate up to the
    Runs DB's errors_summary so silently-broken Favorite slugs are visible.

    `since_epoch` is not used here — favorites adapters return all active
    jobs; the runner's prefilter or evaluation step handles recency.
    """
    if os.environ.get("FD_DRY_RUN") == "1":
        return [], []

    jobs: list[DiscoveredJob] = []
    errors: list[str] = []
    for fav in read_active():
        if not fav.ats_type:
            continue
        try:
            if fav.ats_type == "workday":
                # Workday config (tenant + pod + site) lives in careers_url;
                # _workday_jobs region-filters before the JD fetch.
                if not fav.careers_url:
                    continue
                fav_jobs, err = _workday_jobs(fav, profile)
            else:
                # Every other adapter keys off ats_slug + a flat active-id set.
                if not fav.ats_slug:
                    continue
                active_ids, err = active_ids_for(
                    fav.ats_type, fav.ats_slug, careers_url=fav.careers_url)
                fav_jobs = [
                    _convert(job_id, fav.name, fav.ats_slug, fav.ats_type,
                             fav.careers_url)
                    for job_id in (active_ids or ())
                ]
            if err:
                msg = f"Favorites/{fav.name} ({fav.ats_type}:{fav.ats_slug}): {err}"
                print(f"  [{msg}]")
                errors.append(msg)
                continue
            jobs.extend(fav_jobs)
        except Exception as e:
            msg = f"Favorites/{fav.name}: {type(e).__name__}: {e}"
            print(f"  [{msg}]")
            errors.append(msg)
    return jobs, errors
