"""End-to-end smoke test — runs in dry-run mode, no Notion or LLM calls.

Validates:
  - Every module imports
  - `python3 -m orchestrator <stage>` works for the full /fd-run stage chain
    (discovery → aggregate → jd_fetch → postjd_screen_apply → write → finalize),
    in sequence, on empty discovery AND on synthetic non-empty fixtures
    (Pursue path + Consider path)
  - `postjd_screen_apply` deletes the scorer-input for `drop` verdicts only
  - The /fd-rescore cycle (`rescore_select` → `rescore_apply`) runs end-to-end
  - `python3 -m recycle_feedback <command>` works for prepare + apply, plus
    the apply-with-missing-output resilience path
  - Webhook formatting renders without exceptions

Run:  FD_DRY_RUN=1 python3 tests/smoke.py
Exit: 0 on success, 1 on any failure.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

# The full /fd-run pipeline, in dispatch order.
RUN_CHAIN = ("discovery", "aggregate", "jd_fetch", "postjd_screen_apply",
             "write", "finalize")

failures: list[str] = []


def _check(label: str, cond: bool, detail: str = "") -> None:
    """Record pass/fail for a check."""
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        failures.append(label)


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run a python module from the repo root with FD_DRY_RUN=1."""
    env = {**os.environ, "FD_DRY_RUN": "1"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        args, cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=60,
    )


def _err_tail(r: subprocess.CompletedProcess) -> str:
    """Last stderr line, for failure detail."""
    return r.stderr.strip().split("\n")[-1] if r.returncode else ""


def test_imports() -> None:
    print("\n[imports]")
    modules = [
        "orchestrator", "recycle_feedback",
        "discovery.runner", "discovery.dedup", "discovery.prefilter",
        "discovery.sources.consider", "discovery.sources.getro",
        "discovery.sources.favorites",
        "evaluation.jd_fetch", "evaluation.ats_adapters",
        "notify.webhook",
        "state.profile", "state.tracker", "state.runs",
        "state.notion_client", "state.config", "state.favorites",
        "setup.wizard", "setup.notion_init", "setup.runner",
        "config.vcs", "config.ai50_seed",
    ]
    for m in modules:
        r = _run([PYTHON, "-c", f"import {m}"])
        _check(f"import {m}", r.returncode == 0, detail=_err_tail(r))


def test_orchestrator_empty_chain() -> None:
    print("\n[orchestrator · empty chain]")
    run_id = "smoke-empty"
    shutil.rmtree(f"/tmp/fd-run/{run_id}", ignore_errors=True)
    for stage in RUN_CHAIN:
        r = _run([PYTHON, "-m", "orchestrator", stage, run_id])
        _check(f"stage `{stage}` exit 0", r.returncode == 0, detail=_err_tail(r))
    result_path = Path(f"/tmp/fd-run/{run_id}/finalize-result.json")
    _check("finalize-result.json exists", result_path.exists())
    if result_path.exists():
        data = json.loads(result_path.read_text())
        _check("finalize result has runs_page_id", "runs_page_id" in data)
        _check("finalize pursue_count == 0", data.get("pursue_count") == 0)
    stats_path = Path(f"/tmp/fd-run/{run_id}/postjd-screen-stats.json")
    _check("postjd-screen-stats.json written", stats_path.exists())


def _synthetic_candidate(url: str) -> dict:
    """A minimal candidate dict accepted by the orchestrator stages."""
    return {
        "canonical_url": url,
        "title": "Senior PM, Platform", "company_name": "Anthropic",
        "raw_location": ["Berlin"], "work_mode": "remote",
        "posted_at": "2026-05-10T00:00:00+00:00",
        "source_platform": "Consider", "company_slug": "anthropic",
        "source_job_id": url.rsplit("/", 1)[-1], "vc_source": "a16z",
        "seniority": "senior",
        "salary_disclosed": False, "salary_min_yearly": None,
    }


def _write_synthetic_fixture(wd: Path, c: dict, scorer_tier: str) -> None:
    """Lay down the /tmp work-dir files an aggregate→finalize run consumes."""
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "candidates.json").write_text(json.dumps([c]))
    (wd / "discovery-metrics.json").write_text(json.dumps({
        "variant": "EU", "started_at_iso": "2026-05-14T09:00:00+00:00",
        "profile_hash": "h", "discovery_total": 1, "after_prefilter": 1,
        "effective_window_days": 14, "profile_window_days": 14,
        "closed_count": 0,
    }))
    (wd / "profile.json").write_text(json.dumps({
        "variant": "EU", "interest_description": "PM",
    }))
    (wd / "screener-verdicts-0.json").write_text(json.dumps([
        {"canonical_url": c["canonical_url"], "verdict": "keep", "reason": "PM match"},
    ]))
    (wd / "scorer-input-0.json").write_text(json.dumps({"candidate": c}))
    (wd / "scorer-output-0.json").write_text(json.dumps({
        "tier": scorer_tier, "reasoning": f"{scorer_tier} match",
        "pursue_blockers_detected": [], "stretch_indicators_detected": [],
        "residency_ok": True,
    }))
    (wd / "jd-failed.json").write_text("[]")


def test_orchestrator_synthetic_pursue() -> None:
    print("\n[orchestrator · synthetic Pursue fixture]")
    run_id = "smoke-synth"
    wd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)
    _write_synthetic_fixture(
        wd, _synthetic_candidate(
            "https://jobs.a16z.com/companies/anthropic/jobs/999"), "Strong")

    for stage in ("aggregate", "postjd_screen_apply", "write", "finalize"):
        r = _run([PYTHON, "-m", "orchestrator", stage, run_id])
        _check(f"stage `{stage}` exit 0", r.returncode == 0, detail=_err_tail(r))

    verdicts = json.loads((wd / "all-verdicts.json").read_text())
    _check("verdict count == 1", len(verdicts) == 1)
    _check("tier normalized to Strong — Pursue",
           bool(verdicts) and verdicts[0].get("match") == "Strong — Pursue")

    result = json.loads((wd / "finalize-result.json").read_text())
    _check("finalize pursue_count == 1", result.get("pursue_count") == 1)
    _check("webhook_status == dry-run (Pursue + DRY_RUN)",
           result.get("webhook_status") == "dry-run")


def test_orchestrator_synthetic_consider() -> None:
    print("\n[orchestrator · synthetic Consider fixture]")
    run_id = "smoke-consider"
    wd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)
    _write_synthetic_fixture(
        wd, _synthetic_candidate(
            "https://jobs.a16z.com/companies/anthropic/jobs/888"), "Decent")

    for stage in ("aggregate", "postjd_screen_apply", "write", "finalize"):
        r = _run([PYTHON, "-m", "orchestrator", stage, run_id])
        _check(f"stage `{stage}` exit 0", r.returncode == 0, detail=_err_tail(r))

    verdicts = json.loads((wd / "all-verdicts.json").read_text())
    _check("verdict count == 1", len(verdicts) == 1)
    _check("tier normalized to Decent — Consider",
           bool(verdicts) and verdicts[0].get("match") == "Decent — Consider")

    result = json.loads((wd / "finalize-result.json").read_text())
    _check("finalize pursue_count == 0 (Consider is not Pursue)",
           result.get("pursue_count") == 0)


def test_postjd_screen_apply_drop() -> None:
    print("\n[orchestrator · postjd_screen_apply drop]")
    run_id = "smoke-postjd"
    wd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)

    url_a = "https://jobs.example.com/a"   # will be dropped
    url_b = "https://jobs.example.com/b"   # will be kept
    (wd / "scorer-input-0.json").write_text(
        json.dumps({"candidate": {"canonical_url": url_a}}))
    (wd / "scorer-input-1.json").write_text(
        json.dumps({"candidate": {"canonical_url": url_b}}))
    (wd / "postjd-verdicts-0.json").write_text(json.dumps([
        {"canonical_url": url_a, "verdict": "drop", "reason": "out of region"},
        {"canonical_url": url_b, "verdict": "keep", "reason": "in region"},
    ]))

    r = _run([PYTHON, "-m", "orchestrator", "postjd_screen_apply", run_id])
    _check("stage `postjd_screen_apply` exit 0", r.returncode == 0, detail=_err_tail(r))
    _check("dropped candidate's scorer-input deleted",
           not (wd / "scorer-input-0.json").exists())
    _check("kept candidate's scorer-input retained",
           (wd / "scorer-input-1.json").exists())

    stats = json.loads((wd / "postjd-screen-stats.json").read_text())
    _check("stats: 1 dropped", stats.get("postjd_dropped") == 1)
    _check("stats: 1 kept", stats.get("postjd_kept") == 1)
    _check("stats: 2 screened", stats.get("postjd_screened") == 2)


def test_rescore_cycle() -> None:
    print("\n[orchestrator · rescore cycle]")
    run_id = "smoke-rescore"
    wd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)

    # rescore_select reads rescore-rows-fixture.json in dry-run (Notion-free).
    (wd / "rescore-rows-fixture.json").write_text(json.dumps([{
        "canonical_url": "https://jobs.a16z.com/companies/acme/jobs/777",
        "title": "Staff Product Manager", "company": "Acme",
        "location": "Berlin", "seniority": "staff",
        "vc_source": "a16z", "source_platform": "Consider",
        "page_id": "page-777", "match_prior": "Decent — Consider",
        "match_quality": "OK", "feedback": "", "pass_b_attempts": 1,
        "jd_text": "We are hiring a Staff PM for our infra team in Berlin.",
    }]))

    r = _run([PYTHON, "-m", "orchestrator", "rescore_select", run_id, "failed"])
    _check("stage `rescore_select` exit 0", r.returncode == 0, detail=_err_tail(r))
    selected = json.loads((wd / "rescore-selected.json").read_text())
    _check("rescore selected 1 row", len(selected) == 1)
    _check("rescore_select wrote scorer-input-0.json",
           (wd / "scorer-input-0.json").exists())

    # Synthesize the scorer's verdict, then apply.
    (wd / "scorer-output-0.json").write_text(json.dumps({
        "tier": "Strong", "reasoning": "Strong infra PM match",
        "pursue_blockers_detected": [], "stretch_indicators_detected": [],
        "residency_ok": True,
    }))
    r = _run([PYTHON, "-m", "orchestrator", "rescore_apply", run_id, "failed"])
    _check("stage `rescore_apply` exit 0", r.returncode == 0, detail=_err_tail(r))

    verdicts = json.loads((wd / "all-verdicts.json").read_text())
    _check("rescore produced 1 verdict", len(verdicts) == 1)
    _check("rescore verdict normalized to Strong — Pursue",
           bool(verdicts) and verdicts[0].get("match") == "Strong — Pursue")
    webhook = json.loads((wd / "webhook-verdicts.json").read_text())
    _check("failed-mode rescore flags newly-Pursue for webhook", len(webhook) == 1)


def test_recycle_feedback() -> None:
    print("\n[recycle_feedback]")
    run_id = "smoke-recycle"
    wd = Path(f"/tmp/fd-recycle/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)

    r = _run([PYTHON, "-m", "recycle_feedback", "prepare", run_id])
    _check("prepare exit 0", r.returncode == 0, detail=_err_tail(r))
    _check("feedback-input.json written", (wd / "feedback-input.json").exists())

    # Resilience: apply with no qa-output
    r = _run([PYTHON, "-m", "recycle_feedback", "apply", run_id])
    _check("apply (no qa-output) exits cleanly", r.returncode == 0, detail=_err_tail(r))
    _check("apply warns about missing output", "qa-output missing" in r.stdout)

    # Happy path: synth qa-output then apply
    (wd / "qa-output.json").write_text(json.dumps({
        "learned_exclusions": "Drop defense.",
        "learned_examples": "Match: infra PM.",
        "rationale": "Synthetic test.",
    }))
    r = _run([PYTHON, "-m", "recycle_feedback", "apply", run_id])
    _check("apply (with qa-output) exit 0", r.returncode == 0, detail=_err_tail(r))
    _check("apply logs rationale", "Synthetic test" in r.stdout)


def test_webhook_formatting() -> None:
    print("\n[webhook formatting]")
    r = _run([PYTHON, "-c",
              "from notify.webhook import format_match_message; "
              "print(format_match_message('summary', "
              "[{'match':'Strong — Pursue','title':'t','company':'c',"
              "  'location':'l','salary':'s','seniority':'sr',"
              "  'why_fits':'w','canonical_url':'u'}], "
              "{'variant':'EU','started_at_iso':'2026-05-14T09:00:00+00:00',"
              " 'total_new':1,'pursue_count':1,'consider_count':0,"
              " 'skim_count':0,'cost_usd':0.0}))"])
    _check("format_match_message renders", r.returncode == 0, detail=_err_tail(r))
    _check("output contains Apply: URL", "Apply: u" in r.stdout)


def main() -> int:
    print("Funded Drop smoke test (FD_DRY_RUN=1)")
    test_imports()
    test_orchestrator_empty_chain()
    test_orchestrator_synthetic_pursue()
    test_orchestrator_synthetic_consider()
    test_postjd_screen_apply_drop()
    test_rescore_cycle()
    test_recycle_feedback()
    test_webhook_formatting()

    # Cleanup smoke artifacts
    for path in ("/tmp/fd-run/smoke-empty", "/tmp/fd-run/smoke-synth",
                 "/tmp/fd-run/smoke-consider", "/tmp/fd-run/smoke-postjd",
                 "/tmp/fd-run/smoke-rescore", "/tmp/fd-recycle/smoke-recycle"):
        shutil.rmtree(path, ignore_errors=True)

    print(f"\n{'='*50}")
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED: all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
