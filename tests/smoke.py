"""End-to-end smoke test — runs in dry-run mode, no Notion or LLM calls.

Validates:
  - Every module imports
  - `python3 -m orchestrator <stage>` works for all 5 stages, in sequence, on
    empty discovery AND on a synthetic non-empty fixture (closure + Pursue paths)
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


def test_imports() -> None:
    print("\n[imports]")
    modules = [
        "orchestrator", "recycle_feedback", "cost",
        "discovery.runner", "discovery.dedup", "discovery.prefilter",
        "discovery.sources.consider", "discovery.sources.getro",
        "discovery.sources.favorites",
        "evaluation.jd_fetch",
        "notify.webhook",
        "state.profile", "state.tracker", "state.runs",
        "state.notion_client", "state.config", "state.favorites",
        "setup.wizard", "setup.notion_init", "setup.runner",
        "config.vcs", "config.ai50_seed",
    ]
    for m in modules:
        r = _run([PYTHON, "-c", f"import {m}"])
        _check(f"import {m}", r.returncode == 0,
               detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")


def test_orchestrator_empty_chain() -> None:
    print("\n[orchestrator · empty chain]")
    run_id = "smoke-empty"
    shutil.rmtree(f"/tmp/fd-run/{run_id}", ignore_errors=True)
    for stage in ("discovery", "aggregate", "jd_fetch", "write", "finalize"):
        r = _run([PYTHON, "-m", "orchestrator", stage, run_id])
        _check(f"stage `{stage}` exit 0", r.returncode == 0,
               detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")
    result_path = Path(f"/tmp/fd-run/{run_id}/finalize-result.json")
    _check("finalize-result.json exists", result_path.exists())
    if result_path.exists():
        data = json.loads(result_path.read_text())
        _check("finalize result has runs_page_id", "runs_page_id" in data)
        _check("finalize pursue_count == 0", data.get("pursue_count") == 0)


def test_orchestrator_synthetic_pursue() -> None:
    print("\n[orchestrator · synthetic Pursue fixture]")
    run_id = "smoke-synth"
    wd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True)

    c = {
        "canonical_url": "https://jobs.a16z.com/companies/anthropic/jobs/999",
        "title": "Senior PM, Platform", "company_name": "Anthropic",
        "raw_location": ["Berlin"], "work_mode": "remote",
        "posted_at": "2026-05-10T00:00:00+00:00",
        "source_platform": "Consider", "company_slug": "anthropic",
        "source_job_id": "999", "vc_source": "a16z", "seniority": "senior",
        "salary_disclosed": False, "salary_min_yearly": None,
    }
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
        "tier": "Strong", "reasoning": "Strong match",
        "pursue_blockers_detected": [], "stretch_indicators_detected": [],
        "residency_ok": True,
    }))
    (wd / "jd-failed.json").write_text("[]")

    for stage in ("aggregate", "write", "finalize"):
        r = _run([PYTHON, "-m", "orchestrator", stage, run_id])
        _check(f"stage `{stage}` exit 0", r.returncode == 0,
               detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")

    verdicts = json.loads((wd / "all-verdicts.json").read_text())
    _check("verdict count == 1", len(verdicts) == 1)
    _check("tier normalized to Strong — Pursue",
           verdicts and verdicts[0].get("match") == "Strong — Pursue")

    result = json.loads((wd / "finalize-result.json").read_text())
    _check("finalize pursue_count == 1", result.get("pursue_count") == 1)
    _check("webhook_status == dry-run (Pursue + DRY_RUN)",
           result.get("webhook_status") == "dry-run")


def test_recycle_feedback() -> None:
    print("\n[recycle_feedback]")
    run_id = "smoke-recycle"
    wd = Path(f"/tmp/fd-recycle/{run_id}")
    shutil.rmtree(wd, ignore_errors=True)

    r = _run([PYTHON, "-m", "recycle_feedback", "prepare", run_id])
    _check("prepare exit 0", r.returncode == 0,
           detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")
    _check("feedback-input.json written", (wd / "feedback-input.json").exists())

    # Resilience: apply with no qa-output
    r = _run([PYTHON, "-m", "recycle_feedback", "apply", run_id])
    _check("apply (no qa-output) exits cleanly", r.returncode == 0,
           detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")
    _check("apply warns about missing output", "qa-output missing" in r.stdout)

    # Happy path: synth qa-output then apply
    (wd / "qa-output.json").write_text(json.dumps({
        "learned_exclusions": "Drop defense.",
        "learned_examples": "Match: infra PM.",
        "rationale": "Synthetic test.",
    }))
    r = _run([PYTHON, "-m", "recycle_feedback", "apply", run_id])
    _check("apply (with qa-output) exit 0", r.returncode == 0,
           detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")
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
    _check("format_match_message renders", r.returncode == 0,
           detail=r.stderr.strip().split("\n")[-1] if r.returncode else "")
    _check("output contains Apply: URL",
           "Apply: u" in r.stdout)


def test_cost_estimation() -> None:
    print("\n[cost estimation]")
    sys.path.insert(0, str(REPO_ROOT))
    import cost

    run_id = "smoke-cost"
    rd = Path(f"/tmp/fd-run/{run_id}")
    shutil.rmtree(rd, ignore_errors=True)
    rd.mkdir(parents=True, exist_ok=True)

    # Empty run → zero cost, no agents.
    empty = cost.estimate_run_cost(run_id)
    _check("empty run costs 0.0", empty["cost_usd"] == 0.0)
    _check("empty run has no agents", empty["by_agent"] == {})

    # Synthetic agent I/O: 1 screener batch, 2 scorer calls, 1 summarize.
    (rd / "candidates-batch-0.json").write_text("c" * 3800)
    (rd / "screener-verdicts-0.json").write_text("v" * 760)
    for i in range(2):
        (rd / f"scorer-input-{i}.json").write_text("j" * 19000)
        (rd / f"scorer-output-{i}.json").write_text("o" * 1900)
    (rd / "summarize-input.json").write_text("s" * 3800)
    (rd / "summary.json").write_text("r" * 760)

    r = cost.estimate_run_cost(run_id)
    by = r["by_agent"]
    _check("screener call counted", by.get("screener", {}).get("calls") == 1)
    _check("scorer calls counted", by.get("scorer", {}).get("calls") == 2)
    _check("summarize call counted", by.get("summarize", {}).get("calls") == 1)
    _check("screener priced as haiku", by.get("screener", {}).get("model") == "haiku")
    _check("scorer priced as opus", by.get("scorer", {}).get("model") == "opus")
    _check("total cost positive", r["cost_usd"] > 0)
    _check("total == sum of per-agent costs",
           abs(r["cost_usd"] - round(sum(a["cost_usd"] for a in by.values()), 4)) < 1e-9)
    _check("scorer (Opus) dominates cost",
           by["scorer"]["cost_usd"] > by["screener"]["cost_usd"])
    _check("flagged as estimate", r["estimated"] is True)
    _check("idempotent",
           cost.estimate_run_cost(run_id)["cost_usd"] == r["cost_usd"])


def main() -> int:
    print("Funded Drop smoke test (FD_DRY_RUN=1)")
    test_imports()
    test_orchestrator_empty_chain()
    test_orchestrator_synthetic_pursue()
    test_recycle_feedback()
    test_webhook_formatting()
    test_cost_estimation()

    # Cleanup smoke artifacts
    for path in ("/tmp/fd-run/smoke-empty", "/tmp/fd-run/smoke-synth",
                 "/tmp/fd-run/smoke-cost", "/tmp/fd-recycle/smoke-recycle"):
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
