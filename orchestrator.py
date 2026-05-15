"""Orchestrator — deterministic stage functions invoked by /fd-run skill.

Under Pattern B (Claude Code agent dispatch), the /fd-run skill is the real
orchestrator. This module exposes stage functions that the skill calls between
agent dispatches, intermixing Python (deterministic) and agents (LLM).

Cloud Routine compatible: all inter-stage state lives in /tmp/fd-run/{run_id}/
which is per-fire ephemeral. Cross-fire state lives only in Notion.

Skill flow:
  1. python3 -m orchestrator discovery {run_id}
     → writes candidates-batch-{N}.json + discovery-metrics.json + profile.json
  2. (skill dispatches screener agent on each batch file in waves of ≤8,
      collects JSON verdict arrays, writes screener-verdicts-{N}.json)
  3. python3 -m orchestrator aggregate {run_id}
     → writes screener-survivors.json
  4. python3 -m orchestrator jd_fetch {run_id}
     → writes scorer-input-{idx}.json per survivor with JD; jd-failed.json
  5. (skill dispatches scorer agent on each scorer-input-*.json in waves,
      writes scorer-output-{idx}.json per result)
  6. python3 -m orchestrator write {run_id}
     → writes Tracker rows; summarize-input.json
  7. (skill dispatches summarize agent on summarize-input.json,
      writes summary.json)
  8. python3 -m orchestrator finalize {run_id}
     → writes Runs row; POSTs webhook if Pursue rows exist
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from discovery.runner import run as discovery_run
from discovery.sources.base import DiscoveredJob
from evaluation.jd_fetch import fetch as jd_fetch
from notify.webhook import format_pursue_message, post_webhook
from state.profile import Profile, read as profile_read
from state.runs import create as runs_create, get_last_fire_epoch
from state.tracker import (
    mark_closed_batch,
    read_rows_for_rescore,
    read_url_index,
    update_evaluated,
    write_evaluated,
)
from evaluation.jd_fetch import fetch_jd_for_url


BATCH_SIZE = 15  # candidates per screener batch

# Closure detection: a Tracker row is marked Closed only if its vc_source
# returned at least this many jobs this fire. Protects against per-source
# failures that would otherwise falsely close everything from that source.
MIN_PER_SOURCE_FOR_CLOSURE = 5


def _work_dir(run_id: str) -> Path:
    d = Path("/tmp/fd-run") / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json_or(path: Path, default):
    """Read a JSON file, returning `default` if missing or malformed.

    Routine-resilience: each stage tolerates missing/corrupt upstream output so
    the fire always reaches finalize and a Runs row gets written.
    """
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _job_to_dict(j: DiscoveredJob) -> dict:
    """Serialize DiscoveredJob for inter-stage files. Drops 'raw' to keep size sane."""
    d = asdict(j)
    d["posted_at"] = j.posted_at.isoformat()
    d.pop("raw", None)
    # Preserve essential raw fields needed by jd_fetch for Getro
    if j.source_platform == "Getro" and j.raw:
        d["_getro_slug"] = j.raw.get("slug")
        d["_getro_org_slug"] = (j.raw.get("organization") or {}).get("slug")
    return d


def _profile_to_dict(p: Profile) -> dict:
    """Profile fields needed by agents (omit system/sensitive fields)."""
    return {
        "variant": p.variant,
        "home_country": p.home_country,
        "willing_to_relocate": p.willing_to_relocate,
        "accepted_seniority": p.accepted_seniority,
        "interest_description": p.interest_description,
        "pursue_blockers": p.pursue_blockers,
        "stretch_indicators": p.stretch_indicators,
        "cv_summary": p.cv_summary,
        "learned_exclusions": p.learned_exclusions,
        "learned_examples": p.learned_examples,
        "salary_floor_amount": p.salary_floor_amount,
        "salary_floor_currency": p.salary_floor_currency,
    }


def _dry_run_profile() -> Profile:
    return Profile(
        variant="EU", home_country="Czechia", home_city="Prague",
        work_modes=["Remote", "Hybrid"], accepted_seniority=["senior", "staff"],
        interest_description="dry-run test profile",
        profile_hash="dry-run-hash",
    )


# ─── Stage 1: Discovery ────────────────────────────────────────────────

def discovery_stage(run_id: str) -> dict:
    """Run deterministic Discovery + prefilter. Batch survivors for screener.

    Outputs:
        candidates-batch-{N}.json — input for screener agent (one per batch)
        candidates.json — flat list of all candidates (for later stages)
        profile.json — user profile fields the agents need
        discovery-metrics.json — counts, timing, recovery flag
    """
    wd = _work_dir(run_id)
    started_at = datetime.now(timezone.utc).isoformat()

    profile = _dry_run_profile() if os.environ.get("FD_DRY_RUN") == "1" else profile_read()

    tracker_index = read_url_index()
    tracker_urls = set(tracker_index.keys())
    last_fire_epoch = get_last_fire_epoch()

    candidates, dmetrics = discovery_run(profile, tracker_urls, last_fire_at_epoch=last_fire_epoch)

    # Closure detection: for each active Tracker row whose vc_source is verified
    # alive this fire (≥ MIN_PER_SOURCE_FOR_CLOSURE jobs returned), check whether
    # its URL is still present. If absent, mark Closed. Favorites (vc_source=None)
    # are skipped — their closure requires per-company tracking, deferred.
    per_source_urls = {src: set(urls)
                       for src, urls in (dmetrics.get("per_source_urls") or {}).items()}
    healthy_sources = {src for src, urls in per_source_urls.items()
                       if len(urls) >= MIN_PER_SOURCE_FOR_CLOSURE}
    to_close = [
        row for url, row in tracker_index.items()
        if row.vc_source in healthy_sources
        and url not in per_source_urls.get(row.vc_source, set())
    ]
    closure_result = mark_closed_batch(to_close)
    print(f"  closure: {closure_result['closed']} marked Closed, "
          f"{closure_result['failed']} failed; "
          f"healthy sources: {sorted(healthy_sources)}")

    profile_dict = _profile_to_dict(profile)
    profile_dict_path = wd / "profile.json"
    profile_dict_path.write_text(json.dumps(profile_dict, indent=2, default=str))

    candidate_dicts = [_job_to_dict(j) for j in candidates]
    (wd / "candidates.json").write_text(json.dumps(candidate_dicts, indent=2, default=str))

    # Favorites discovery is two-phase (returns IDs only; title/location fill
    # at JD fetch time). They have no structured tags for Pass A to read, so
    # we bypass the screener entirely — they auto-promote straight to JD
    # fetch + Pass B. Saves Pass A LLM cost and avoids the screener marking
    # every empty-title candidate `maybe`.
    vc_candidates = [c for c in candidate_dicts if c.get("source_platform") != "Favorites"]
    fav_candidates = [c for c in candidate_dicts if c.get("source_platform") == "Favorites"]
    (wd / "auto-promote-favorites.json").write_text(
        json.dumps(fav_candidates, indent=2, default=str)
    )

    # Batch VC candidates for parallel screener dispatch
    num_batches = 0
    for i in range(0, len(vc_candidates), BATCH_SIZE):
        batch = vc_candidates[i:i + BATCH_SIZE]
        batch_data = {"candidates": batch, "profile": profile_dict}
        (wd / f"candidates-batch-{num_batches}.json").write_text(
            json.dumps(batch_data, indent=2, default=str)
        )
        num_batches += 1

    # Strip the heavy per_source_urls before persisting metrics — only the counts
    # are needed by downstream stages.
    dmetrics_slim = {k: v for k, v in dmetrics.items() if k != "per_source_urls"}

    metrics = {
        "run_id": run_id,
        "started_at_iso": started_at,
        "variant": profile.variant,
        "profile_hash": profile.profile_hash,
        "tracker_known_urls": len(tracker_urls),
        "num_batches": num_batches,
        "auto_promoted_favorites": len(fav_candidates),
        "closed_count": closure_result["closed"],
        "closure_failed_count": closure_result["failed"],
        "healthy_sources": sorted(healthy_sources),
        **dmetrics_slim,
    }
    (wd / "discovery-metrics.json").write_text(json.dumps(metrics, indent=2, default=str))

    print(f"discovery_stage: {dmetrics['discovery_total']} raw → "
          f"{dmetrics['after_prefilter']} survivors → "
          f"{num_batches} screener batches + {len(fav_candidates)} auto-promoted favorites")
    return metrics


# ─── Stage 2: Screener aggregate ───────────────────────────────────────

def screener_aggregate(run_id: str) -> dict:
    """Read screener-verdicts-{N}.json files, aggregate to survivors + drops.

    Auto-promoted Favorites (from `auto-promote-favorites.json`) merge into
    survivors here without consuming Pass A tokens. They skip the screener
    because their structured tags (title, location, seniority) are blank
    until JD fetch fills them in.
    """
    wd = _work_dir(run_id)
    candidates = _load_json_or(wd / "candidates.json", [])
    cand_by_url = {c["canonical_url"]: c for c in candidates}

    survivors: list[dict] = []
    drops: list[dict] = []

    for f in sorted(wd.glob("screener-verdicts-*.json")):
        try:
            verdicts = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  warn: {f.name} malformed JSON: {e}; skipping")
            continue
        for v in verdicts:
            cand = cand_by_url.get(v.get("canonical_url"))
            if not cand:
                continue
            cand = {**cand, "_pass_a_verdict": v.get("verdict"),
                            "_pass_a_reason": v.get("reason", "")}
            if v.get("verdict") in ("keep", "maybe"):
                survivors.append(cand)
            else:
                drops.append(cand)

    # Auto-promote Favorites: skip Pass A entirely.
    auto_promoted = _load_json_or(wd / "auto-promote-favorites.json", [])
    for cand in auto_promoted:
        survivors.append({
            **cand,
            "_pass_a_verdict": "auto",
            "_pass_a_reason": "Favorites — skipped Pass A (no structured tags at discovery)",
        })

    (wd / "screener-survivors.json").write_text(json.dumps(survivors, indent=2, default=str))
    (wd / "screener-drops.json").write_text(json.dumps(drops, indent=2, default=str))

    stats = {
        "pass_a_evaluated": len(survivors) - len(auto_promoted) + len(drops),
        "pass_a_kept": len(survivors) - len(auto_promoted),
        "pass_a_dropped": len(drops),
        "auto_promoted_favorites": len(auto_promoted),
    }
    (wd / "screener-stats.json").write_text(json.dumps(stats, indent=2))
    print(f"screener_aggregate: {stats['pass_a_evaluated']} screened → "
          f"{stats['pass_a_kept']} survivors, {stats['pass_a_dropped']} dropped"
          f" (+{stats['auto_promoted_favorites']} auto-promoted favorites)")
    return stats


# ─── Stage 3: JD fetch ─────────────────────────────────────────────────

def jd_fetch_stage(run_id: str) -> dict:
    """Fetch JD for each screener survivor. Write per-survivor scorer-input files.

    Favorites post-filter: Favorites bypass Pass A at discovery because their
    title/location/work_mode/salary are blank then (two-phase ATS adapter).
    After JD fetch enriches them, re-apply the deterministic prefilter (S2–S9)
    so non-CZ hybrid/onsite Favorites are dropped before the expensive Pass B,
    matching the same checks VC candidates went through at discovery.
    """
    from discovery.prefilter import apply as apply_prefilter
    from state.profile import Profile

    wd = _work_dir(run_id)
    survivors = _load_json_or(wd / "screener-survivors.json", [])
    profile_dict = _load_json_or(wd / "profile.json", {})

    # Reconstruct a Profile from the persisted dict for the post-filter call.
    # Only fields prefilter actually reads are populated; everything else
    # falls back to defaults.
    profile_for_filter = Profile(
        variant=profile_dict.get("variant", "EU"),
        eu_include_uk_ie=profile_dict.get("eu_include_uk_ie", False),
        home_country=profile_dict.get("home_country", ""),
        work_modes=profile_dict.get("work_modes", []),
        search_outside_home=profile_dict.get("search_outside_home", False),
        willing_to_relocate=profile_dict.get("willing_to_relocate", False),
        accepted_seniority=profile_dict.get("accepted_seniority", []),
        salary_floor_amount=profile_dict.get("salary_floor_amount"),
        salary_floor_currency=profile_dict.get("salary_floor_currency", "USD"),
        excluded_companies=profile_dict.get("excluded_companies", []),
        excluded_industries=profile_dict.get("excluded_industries", []),
    )

    successes = 0
    failures: list[dict] = []
    post_filter_dropped: list[dict] = []

    for idx, cand in enumerate(survivors):
        # Reconstruct minimal DiscoveredJob for jd_fetch
        try:
            posted_at = datetime.fromisoformat(
                cand["posted_at"].replace("Z", "+00:00")
            ) if isinstance(cand.get("posted_at"), str) else datetime.now(timezone.utc)
        except (ValueError, AttributeError):
            posted_at = datetime.now(timezone.utc)

        # Rehydrate Getro raw fields if present
        raw = {}
        if cand.get("_getro_slug") or cand.get("_getro_org_slug"):
            raw = {
                "slug": cand.get("_getro_slug"),
                "organization": {"slug": cand.get("_getro_org_slug")},
            }

        job = DiscoveredJob(
            canonical_url=cand["canonical_url"],
            title=cand.get("title", ""),
            company_name=cand.get("company_name", ""),
            raw_location=cand.get("raw_location", []),
            work_mode=cand.get("work_mode", "on_site"),
            posted_at=posted_at,
            source_platform=cand.get("source_platform", "Consider"),
            raw=raw,
            company_slug=cand.get("company_slug"),
            source_job_id=cand.get("source_job_id"),
            vc_source=cand.get("vc_source"),
        )

        jd_text, jd_meta, err = jd_fetch(job)
        if jd_text:
            # Merge ATS-parsed metadata into the candidate dict. Favorites
            # discovery leaves title/location/work_mode/salary blank — this is
            # where they get populated. For VC sources the discovery values
            # win unless the ATS provides something cleaner.
            enriched = {**cand, "jd_text": jd_text}
            if jd_meta.get("title") and not cand.get("title"):
                enriched["title"] = jd_meta["title"]
            if jd_meta.get("location") and not (cand.get("raw_location") or [None])[0]:
                enriched["raw_location"] = [jd_meta["location"]]
            if jd_meta.get("work_mode") and cand.get("work_mode") == "on_site":
                enriched["work_mode"] = jd_meta["work_mode"]
            if jd_meta.get("salary_disclosed") and not cand.get("salary_disclosed"):
                enriched["salary_disclosed"]  = True
                enriched["salary_min_yearly"] = jd_meta.get("salary_min_yearly")
                enriched["salary_max_yearly"] = jd_meta.get("salary_max_yearly")
                enriched["salary_currency"]   = jd_meta.get("salary_currency")

            # Post-filter Favorites against the same S2-S9 the VC candidates
            # went through at discovery — now that their structured fields
            # are populated. Drops non-CZ hybrid/onsite Favorites before the
            # expensive Pass B scorer call.
            if cand.get("source_platform") == "Favorites":
                # Build a minimal DiscoveredJob from the enriched dict for
                # the existing prefilter to chew on. Keep the same posted_at
                # we already parsed up top.
                rerun_job = DiscoveredJob(
                    canonical_url=enriched["canonical_url"],
                    title=enriched.get("title", ""),
                    company_name=enriched.get("company_name", ""),
                    raw_location=enriched.get("raw_location", []),
                    work_mode=enriched.get("work_mode", "on_site"),
                    posted_at=posted_at,
                    source_platform="Favorites",
                    raw={},
                    seniority=enriched.get("seniority"),
                    salary_disclosed=bool(enriched.get("salary_disclosed")),
                    salary_min_yearly=enriched.get("salary_min_yearly"),
                    salary_max_yearly=enriched.get("salary_max_yearly"),
                    salary_currency=enriched.get("salary_currency"),
                    industry_tags=enriched.get("industry_tags", []),
                )
                kept, _counts = apply_prefilter([rerun_job], profile_for_filter)
                if not kept:
                    post_filter_dropped.append({
                        "canonical_url": enriched["canonical_url"],
                        "title": enriched.get("title", ""),
                        "company": enriched.get("company_name", ""),
                        "reason": (
                            f"post-JD prefilter dropped — "
                            f"work_mode={enriched.get('work_mode')}, "
                            f"location={(enriched.get('raw_location') or [''])[0]}"
                        ),
                    })
                    continue  # skip writing scorer-input

            scorer_input = {
                "candidate": enriched,
                "profile": profile_dict,
            }
            (wd / f"scorer-input-{idx}.json").write_text(
                json.dumps(scorer_input, indent=2, default=str)
            )
            successes += 1
        else:
            failures.append({**cand, "_jd_fetch_error": err})

    (wd / "jd-failed.json").write_text(json.dumps(failures, indent=2, default=str))
    (wd / "post-filter-dropped.json").write_text(
        json.dumps(post_filter_dropped, indent=2, default=str)
    )
    stats = {
        "jd_fetched_ok": successes,
        "jd_fetch_failed": len(failures),
        "post_filter_dropped": len(post_filter_dropped),
    }
    (wd / "jd-fetch-stats.json").write_text(json.dumps(stats, indent=2))
    print(f"jd_fetch_stage: {successes} JDs ok, {len(failures)} failed, "
          f"{len(post_filter_dropped)} dropped by post-JD prefilter (Favorites)")
    return stats


# ─── Stage 4: Tracker write + summarize input ─────────────────────────

_TIER_NORMALIZE = {
    "Strong":  "Strong — Pursue",
    "Decent":  "Decent — Consider",
    "Stretch": "Stretch — Skim",
    # Already-normalized values pass through
    "Strong — Pursue":   "Strong — Pursue",
    "Decent — Consider": "Decent — Consider",
    "Stretch — Skim":    "Stretch — Skim",
}


def write_stage(run_id: str) -> dict:
    """Read scorer outputs + jd-failed, write Tracker rows, build summarize-input.

    Candidate source-of-truth: each scorer-input-<idx>.json carries the
    candidate dict that jd_fetch_stage built — including the ATS-derived
    title/location merged in. Reading from screener-survivors.json instead
    would lose those fields and write empty-titled rows to Notion.
    """
    wd = _work_dir(run_id)
    failures = _load_json_or(wd / "jd-failed.json", [])
    dmetrics = _load_json_or(wd / "discovery-metrics.json", {})

    profile_hash = dmetrics.get("profile_hash", "")
    now_iso = datetime.now(timezone.utc).isoformat()

    verdicts: list[dict] = []
    pursue_samples: list[dict] = []
    consider_samples: list[dict] = []

    # Map scorer outputs back to survivors via input file index
    for f in sorted(wd.glob("scorer-output-*.json")):
        try:
            scored = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  warn: {f.name} malformed JSON: {e}; skipping")
            continue

        # Recover candidate URL via the matching input file
        idx_str = f.stem.replace("scorer-output-", "")
        try:
            idx = int(idx_str)
        except ValueError:
            print(f"  warn: {f.name} has non-numeric index; skipping")
            continue
        input_path = wd / f"scorer-input-{idx}.json"
        if not input_path.exists():
            continue
        # Use the scorer-input's candidate as the source of truth — it carries
        # JD-fetch metadata (title, location, work_mode) that jd_fetch_stage
        # merged in. The screener-survivors version is pre-merge and would
        # leave Favorites rows with empty Title/Location in Tracker.
        cand = json.loads(input_path.read_text())["candidate"]
        cand_url = cand["canonical_url"]

        tier_raw = scored.get("tier", "Stretch")
        tier_norm = _TIER_NORMALIZE.get(tier_raw, "Decent — Consider")  # safe default

        verdict = _build_verdict(cand, cand_url, tier_norm, scored,
                                 now_iso, profile_hash, run_id, status="New")
        verdicts.append(verdict)

        if tier_norm == "Strong — Pursue":
            pursue_samples.append({"title": verdict["title"], "company": verdict["company"]})
        elif tier_norm == "Decent — Consider":
            consider_samples.append({"title": verdict["title"], "company": verdict["company"]})

    # JD-fetch failures get a row with status=jd_fetch_failed
    for fail in failures:
        verdict = _build_verdict(
            fail, fail["canonical_url"], "Decent — Consider",
            {"reasoning": f"JD fetch failed: {fail.get('_jd_fetch_error', 'unknown')}",
             "pursue_blockers_detected": [], "stretch_indicators_detected": [],
             "residency_ok": None},
            now_iso, profile_hash, run_id, status="jd_fetch_failed",
        )
        verdicts.append(verdict)

    try:
        write_result = write_evaluated(verdicts)
    except Exception as e:
        print(f"  warn: write_evaluated raised {type(e).__name__}: {e}; "
              f"continuing so finalize can write Runs row")
        write_result = {
            "written": 0, "failed": len(verdicts),
            "error": f"{type(e).__name__}: {e}",
        }

    sum_input = {
        "metrics": {
            "variant":               dmetrics.get("variant", "EU"),
            "started_at_iso":        dmetrics.get("started_at_iso", now_iso),
            "discovery_total":       dmetrics.get("discovery_total", 0),
            "after_prefilter":       dmetrics.get("after_prefilter", 0),
            "pass_a_evaluated":      len(verdicts) - len(failures),
            "pass_a_kept":           len(verdicts) - len(failures),
            "pass_b_scored":         len(verdicts) - len(failures),
            "pursue_count":          len(pursue_samples),
            "consider_count":        len(consider_samples),
            "skim_count":            sum(1 for v in verdicts if v["match"] == "Stretch — Skim"),
            "closed_count":          dmetrics.get("closed_count", 0),
            "cost_usd":              0.0,
            "duration_s":            0,
            "effective_window_days": dmetrics.get("effective_window_days", 14),
            "profile_window_days":   dmetrics.get("profile_window_days", 14),
            "errors_count":          len(failures) + len(dmetrics.get("source_errors") or []),
            "errors_summary":        _build_errors_summary(
                source_errors=dmetrics.get("source_errors") or [],
                jd_fetch_failed=failures,
                closure_failed=dmetrics.get("closure_failed_count", 0),
            ),
            "recovery_widened":      (
                dmetrics.get("effective_window_days", 14)
                > dmetrics.get("profile_window_days", 14)
            ),
        },
        "samples": {
            "pursue":   pursue_samples[:5],
            "consider": consider_samples[:5],
        },
    }
    (wd / "summarize-input.json").write_text(json.dumps(sum_input, indent=2, default=str))
    (wd / "all-verdicts.json").write_text(json.dumps(verdicts, indent=2, default=str))

    write_result["pursue_count"] = len(pursue_samples)
    print(f"write_stage: {write_result['written']} written, {write_result['failed']} failed; "
          f"{len(pursue_samples)} Pursue, {len(consider_samples)} Consider")
    return write_result


def _build_errors_summary(source_errors: list, jd_fetch_failed: list,
                          closure_failed: int) -> str:
    """One-paragraph errors_summary for the Runs DB row.

    Surfaces per-source fetch failures (Getro 403, Consider VC timeout, etc.),
    JD-fetch failures, and closure-marking failures so the user can see what
    went wrong without reading the raw routine log. Capped at ~1500 chars
    to stay inside Notion's rich-text limits.
    """
    parts: list[str] = []
    if source_errors:
        parts.append(f"Source fetch ({len(source_errors)}): " + "; ".join(source_errors[:10]))
        if len(source_errors) > 10:
            parts.append(f"… and {len(source_errors) - 10} more")
    if jd_fetch_failed:
        parts.append(f"JD fetch failed ({len(jd_fetch_failed)}) — see jd_fetch_failed rows in Tracker")
    if closure_failed:
        parts.append(f"Closure-marking failed ({closure_failed})")
    return " | ".join(parts)[:1500]


def _build_verdict(cand: dict, url: str, tier_norm: str, scored: dict,
                   now_iso: str, profile_hash: str, run_id: str, status: str) -> dict:
    """Build a verdict dict for state.tracker.write_evaluated."""
    pb_detected = scored.get("pursue_blockers_detected", []) or []
    si_detected = scored.get("stretch_indicators_detected", []) or []
    return {
        "canonical_url":               url,
        "title":                       cand.get("title", ""),
        "company":                     cand.get("company_name", ""),
        "location":                    _format_location(cand),
        "match":                       tier_norm,
        "why_fits":                    scored.get("reasoning", ""),
        "salary":                      _format_salary(cand),
        "seniority":                   cand.get("seniority"),
        "posted_at_iso":               cand.get("posted_at", now_iso),
        "status":                      status,
        "source_platform":             cand.get("source_platform"),
        "vc_source":                   cand.get("vc_source"),
        "first_seen_at_iso":           now_iso,
        "last_seen_at_iso":            now_iso,
        "pass_a_verdict":              cand.get("_pass_a_verdict"),
        "pass_a_reason":               cand.get("_pass_a_reason", ""),
        "pass_b_residency_ok":         bool(scored.get("residency_ok")),
        "pass_b_attempts":             1,
        "profile_hash_at_eval":        profile_hash,
        "last_run_id":                 run_id,
        "pursue_blockers_detected":    "; ".join(pb_detected),
        "stretch_indicators_detected": "; ".join(si_detected),
    }


# ─── Stage 5: Finalize (Runs row + webhook) ───────────────────────────

def finalize_stage(run_id: str) -> dict:
    """Read summary.json, write Runs row, POST webhook if Pursue rows."""
    wd = _work_dir(run_id)
    dmetrics = _load_json_or(wd / "discovery-metrics.json", {})
    # If upstream stages skipped (no candidates) summarize-input may be missing —
    # synthesize a minimal one from whatever we have so the Runs row still lands.
    sum_input = _load_json_or(wd / "summarize-input.json", {
        "metrics": {
            "variant":               dmetrics.get("variant", "EU"),
            "started_at_iso":        dmetrics.get("started_at_iso",
                                       datetime.now(timezone.utc).isoformat()),
            "discovery_total":       dmetrics.get("discovery_total", 0),
            "after_prefilter":       dmetrics.get("after_prefilter", 0),
            "pass_a_evaluated":      0, "pass_a_kept":  0, "pass_b_scored": 0,
            "pursue_count":          0, "consider_count": 0, "skim_count": 0,
            "closed_count":          dmetrics.get("closed_count", 0),
            "cost_usd":              0.0, "duration_s":     0,
            "effective_window_days": dmetrics.get("effective_window_days", 14),
            "profile_window_days":   dmetrics.get("profile_window_days", 14),
            "errors_count":          0,
            "recovery_widened":      False,
        },
        "samples": {"pursue": [], "consider": []},
    })
    verdicts = _load_json_or(wd / "all-verdicts.json", [])

    summary_path = wd / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text()).get("summary", "")
        except json.JSONDecodeError:
            summary = ""
    else:
        summary = ""

    # Fallback summary if agent didn't produce one
    if not summary:
        m = sum_input["metrics"]
        parts = [
            f"Found {m['discovery_total']} candidates; surfaced "
            f"{m['pursue_count']} Pursue + {m['consider_count']} Consider + "
            f"{m['skim_count']} Skim."
        ]
        if m.get("closed_count", 0) > 0:
            parts.append(f"Closed {m['closed_count']} stale Tracker rows.")
        summary = " ".join(parts)

    metrics = {**sum_input["metrics"], "total_new": len(verdicts)}

    try:
        runs_page_id = runs_create(
            run_id=run_id,
            started_at_iso=metrics.get("started_at_iso", datetime.now(timezone.utc).isoformat()),
            variant=metrics.get("variant", "EU"),
            summary=summary,
            metrics=metrics,
        )
        runs_error = None
    except Exception as e:
        runs_page_id = ""
        runs_error = f"{type(e).__name__}: {e}"
        print(f"  warn: runs_create failed: {runs_error}; continuing to webhook")

    # Webhook POST. Default: any Pursue row in this fire triggers a notification.
    # Rescore mode writes webhook-verdicts.json with only the rows eligible for
    # webhook (newly-Pursue from `failed` mode; empty for `stale`/`flagged`).
    # When that override file exists, prefer it over all-verdicts.
    webhook_override = wd / "webhook-verdicts.json"
    if webhook_override.exists():
        pursue_verdicts = [v for v in _load_json_or(webhook_override, [])
                           if v.get("match") == "Strong — Pursue"]
    else:
        pursue_verdicts = [v for v in verdicts if v.get("match") == "Strong — Pursue"]
    webhook_status = "skipped"
    if pursue_verdicts:
        if os.environ.get("FD_DRY_RUN") == "1":
            webhook_status = "dry-run"
        else:
            try:
                profile = profile_read()
                if profile.webhook_enabled and profile.webhook_url:
                    message = format_pursue_message(summary, pursue_verdicts, metrics)
                    err = post_webhook(profile.webhook_url, message)
                    webhook_status = "ok" if err is None else f"failed: {err}"
                else:
                    webhook_status = "not_configured"
            except Exception as e:
                webhook_status = f"error: {type(e).__name__}: {e}"

    result = {
        "runs_page_id": runs_page_id,
        "runs_error": runs_error,
        "pursue_count": len(pursue_verdicts),
        "webhook_status": webhook_status,
    }
    (wd / "finalize-result.json").write_text(json.dumps(result, indent=2))
    runs_status = f"written ({runs_page_id})" if runs_page_id else f"FAILED ({runs_error})"
    print(f"finalize_stage: Runs row {runs_status}; "
          f"webhook: {webhook_status}; {len(pursue_verdicts)} Pursue rows")
    return result


# ─── Formatters (shared with state.tracker indirectly) ────────────────

def _format_location(cand: dict) -> str:
    base = (cand.get("raw_location") or ["Unknown"])[0]
    mode = {"remote": "Remote", "hybrid": "Hybrid", "on_site": ""}.get(
        cand.get("work_mode", "on_site"), ""
    )
    return f"{base} ({mode})" if mode else base


def _format_salary(cand: dict) -> str:
    if not cand.get("salary_disclosed") or not cand.get("salary_min_yearly"):
        return "—"
    cur = cand.get("salary_currency") or "USD"
    cur_sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, cur + " ")

    def fmt(n: int) -> str:
        return f"{n // 1000}k" if n >= 10000 else str(n)

    lo = fmt(cand["salary_min_yearly"])
    hi_v = cand.get("salary_max_yearly")
    if hi_v and hi_v != cand["salary_min_yearly"]:
        return f"{cur_sym}{lo}–{cur_sym}{fmt(hi_v)} / yr"
    return f"{cur_sym}{lo} / yr"


# ─── CLI dispatch ─────────────────────────────────────────────────────

# ─── Rescore stages (Pass B re-evaluation of existing Tracker rows) ───

def rescore_select(run_id: str, mode: str = "failed") -> dict:
    """Select Tracker rows for rescore, fetch fresh JDs, write scorer-input files.

    Modes: failed | stale | flagged (see state.tracker.read_rows_for_rescore).
    """
    wd = _work_dir(run_id)
    started_at = datetime.now(timezone.utc).isoformat()

    profile = profile_read()
    profile_dict = _profile_to_dict(profile)
    (wd / "profile.json").write_text(json.dumps(profile_dict, indent=2, default=str))

    rows = read_rows_for_rescore(mode, current_profile_hash=profile.profile_hash)

    (wd / "rescore-selected.json").write_text(json.dumps(rows, indent=2))
    (wd / "rescore-mode.json").write_text(json.dumps({
        "mode": mode,
        "started_at_iso": started_at,
        "variant": profile.variant,
        "profile_hash": profile.profile_hash,
        "selected_count": len(rows),
    }, indent=2))

    successes = 0
    still_failed: list[dict] = []
    for idx, row in enumerate(rows):
        url = row["canonical_url"]
        jd_text, _jd_meta, err = fetch_jd_for_url(url)
        if jd_text:
            candidate = {
                "canonical_url":     url,
                "title":             row.get("title", ""),
                "company_name":      row.get("company", ""),
                "raw_location":      [row.get("location", "")],
                "work_mode":         "remote",  # unknown — scorer reads JD for actual signal
                "seniority":         row.get("seniority"),
                "vc_source":         row.get("vc_source"),
                "source_platform":   row.get("source_platform"),
                "salary_disclosed":  False,
                "salary_min_yearly": None,
                "_page_id":          row["page_id"],
                "jd_text":           jd_text,
            }
            (wd / f"scorer-input-{idx}.json").write_text(json.dumps({
                "candidate": candidate, "profile": profile_dict,
            }, indent=2, default=str))
            successes += 1
        else:
            still_failed.append({**row, "_jd_fetch_error": err})

    (wd / "rescore-jd-failed.json").write_text(json.dumps(still_failed, indent=2))
    print(f"rescore_select [{mode}]: {len(rows)} selected → "
          f"{successes} JDs fetched, {len(still_failed)} still failing")
    return {"mode": mode, "selected": len(rows),
            "fetched": successes, "still_failed": len(still_failed)}


def rescore_apply(run_id: str, mode: str = "failed") -> dict:
    """Read scorer outputs, update existing Tracker rows in-place.

    Webhook eligibility:
      - mode=failed → newly-Pursue rows go to webhook-verdicts.json (notify user)
      - mode=stale/flagged → webhook-verdicts.json stays empty (re-evaluation,
        not new discovery)
    """
    wd = _work_dir(run_id)
    info = _load_json_or(wd / "rescore-mode.json", {})
    profile_hash = info.get("profile_hash", "")
    selected = _load_json_or(wd / "rescore-selected.json", [])
    sel_by_idx = {i: r for i, r in enumerate(selected)}
    still_failed = _load_json_or(wd / "rescore-jd-failed.json", [])

    upserted: list[dict] = []
    newly_pursue: list[dict] = []

    for f in sorted(wd.glob("scorer-output-*.json")):
        try:
            scored = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"  warn: {f.name} malformed: {e}; skipping")
            continue
        idx_str = f.stem.replace("scorer-output-", "")
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        row = sel_by_idx.get(idx)
        if not row:
            continue

        tier_norm = _TIER_NORMALIZE.get(scored.get("tier", "Stretch"), "Decent — Consider")
        verdict = {
            "page_id":                     row["page_id"],
            "canonical_url":               row["canonical_url"],
            "title":                       row["title"],
            "company":                     row["company"],
            "match":                       tier_norm,
            "match_prior":                 row.get("match_prior", ""),
            "why_fits":                    scored.get("reasoning", ""),
            "status":                      "New",
            "pass_b_residency_ok":         bool(scored.get("residency_ok")),
            "pass_b_attempts":             row.get("pass_b_attempts", 0) + 1,
            "profile_hash_at_eval":        profile_hash,
            "last_run_id":                 run_id,
            "pursue_blockers_detected":    "; ".join(scored.get("pursue_blockers_detected", []) or []),
            "stretch_indicators_detected": "; ".join(scored.get("stretch_indicators_detected", []) or []),
            # Pass existing user-owned fields so update_evaluated knows whether
            # the user has already taken a position on this row.
            "existing_match_quality":      row.get("match_quality", "OK"),
            "existing_feedback":           row.get("feedback", ""),
        }
        try:
            update_evaluated(row["page_id"], verdict)
            upserted.append(verdict)
            if mode == "failed" and tier_norm == "Strong — Pursue":
                newly_pursue.append(verdict)
        except Exception as e:
            print(f"  warn: update_evaluated failed for {row.get('canonical_url')}: {e}")

    # Refresh status on rows whose JD fetch is still broken
    for fail in still_failed:
        try:
            update_evaluated(fail["page_id"], {
                "match":                       "Decent — Consider",
                "why_fits":                    f"JD fetch still failing after rescore: {fail.get('_jd_fetch_error','?')}",
                "status":                      "jd_fetch_failed",
                "pass_b_residency_ok":         False,
                "pass_b_attempts":             fail.get("pass_b_attempts", 0) + 1,
                "profile_hash_at_eval":        profile_hash,
                "last_run_id":                 run_id,
                "pursue_blockers_detected":    "",
                "stretch_indicators_detected": "",
            })
        except Exception as e:
            print(f"  warn: update_evaluated failed for {fail.get('canonical_url')}: {e}")

    (wd / "webhook-verdicts.json").write_text(json.dumps(newly_pursue, indent=2))
    (wd / "all-verdicts.json").write_text(json.dumps(upserted, indent=2))

    pursue_c   = sum(1 for v in upserted if v["match"] == "Strong — Pursue")
    consider_c = sum(1 for v in upserted if v["match"] == "Decent — Consider")
    skim_c     = sum(1 for v in upserted if v["match"] == "Stretch — Skim")
    (wd / "summarize-input.json").write_text(json.dumps({
        "metrics": {
            "kind":                  f"rescore [{mode}]",
            "variant":               info.get("variant", "EU"),
            "started_at_iso":        info.get("started_at_iso"),
            "discovery_total":       0,
            "after_prefilter":       0,
            "pass_a_evaluated":      0,
            "pass_a_kept":           0,
            "pass_b_scored":         len(upserted),
            "pursue_count":          pursue_c,
            "consider_count":        consider_c,
            "skim_count":            skim_c,
            "closed_count":          0,
            "cost_usd":              0.0,
            "duration_s":            0,
            "effective_window_days": 0,
            "profile_window_days":   0,
            "errors_count":          len(still_failed),
            "recovery_widened":      False,
            "total_new":             0,
            "rescore_updated":       len(upserted),
            "rescore_newly_pursue":  len(newly_pursue),
        },
        "samples": {
            "pursue":   [{"title": v["title"], "company": v["company"]}
                         for v in upserted if v["match"] == "Strong — Pursue"][:5],
            "consider": [{"title": v["title"], "company": v["company"]}
                         for v in upserted if v["match"] == "Decent — Consider"][:5],
        },
    }, indent=2, default=str))

    print(f"rescore_apply [{mode}]: updated {len(upserted)} rows "
          f"({pursue_c}P/{consider_c}C/{skim_c}S); "
          f"{len(still_failed)} still failing; webhook-eligible: {len(newly_pursue)}")
    return {"updated": len(upserted), "newly_pursue": len(newly_pursue),
            "still_failed": len(still_failed)}


_STAGES = {
    "discovery":      discovery_stage,
    "aggregate":      screener_aggregate,
    "jd_fetch":       jd_fetch_stage,
    "write":          write_stage,
    "finalize":       finalize_stage,
    "rescore_select": rescore_select,
    "rescore_apply":  rescore_apply,
}


def _main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _STAGES:
        print(f"Usage: python3 -m orchestrator <stage> [run_id] [extra-args]\n"
              f"Stages: {', '.join(_STAGES.keys())}\n"
              f"  rescore_select / rescore_apply also take <mode> "
              f"(failed | stale | flagged)", file=sys.stderr)
        return 1
    stage_name = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else uuid.uuid4().hex
    extra_args = sys.argv[3:]
    print(f"=== orchestrator.{stage_name} · run_id={run_id[:8]} ===")
    _STAGES[stage_name](run_id, *extra_args)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
