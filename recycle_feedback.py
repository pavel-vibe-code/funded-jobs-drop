"""Recycle-feedback orchestrator — Notion side of the learning loop.

Three-step flow:

  1. python3 -m recycle_feedback prepare <run_id>
     → reads Tracker feedback rows + current Profile.learned_*
     → writes /tmp/fd-recycle/<run_id>/feedback-input.json

  2. (skill dispatches qa agent on feedback-input.json,
      agent writes qa-output.json)

  3. python3 -m recycle_feedback apply <run_id>
     → reads qa-output.json, updates Profile.learned_exclusions + learned_examples

Cloud Routine compatible: per-fire state in /tmp/fd-recycle/<run_id>/, Notion
is the only persistent store.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

from state.profile import Profile, read as profile_read, update as profile_update
from state.tracker import DROPPED_FEEDBACK_STATUS, read_feedback_rows, update_status


def _work_dir(run_id: str) -> Path:
    d = Path("/tmp/fd-recycle") / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare(run_id: str) -> dict:
    """Read feedback rows + current learned text. Write input file for qa agent."""
    wd = _work_dir(run_id)
    if os.environ.get("FD_DRY_RUN") == "1":
        profile = Profile(learned_exclusions="", learned_examples="")
    else:
        profile = profile_read()
    rows = read_feedback_rows()
    payload = {
        "current": {
            "learned_exclusions": profile.learned_exclusions,
            "learned_examples":   profile.learned_examples,
        },
        "feedback_rows": rows,
    }
    (wd / "feedback-input.json").write_text(json.dumps(payload, indent=2, default=str))
    print(f"prepare: {len(rows)} feedback rows; "
          f"current rules: {len(profile.learned_exclusions)}ch exclusions + "
          f"{len(profile.learned_examples)}ch examples")
    return {"feedback_count": len(rows)}


def _drop_user_rejected(run_id: str) -> int:
    """Archive rows the user explicitly rejected — Status = Dropped (feedback).

    A row counts as user-rejected when Match quality is "Feedback" AND the
    Feedback text is user-typed — i.e. not the scorer's own `[Auto]`-prefixed
    auto-note. Scorer auto-flagged-but-unreviewed rows (`[Auto]` text only) and
    OK rows are deliberately left untouched: the user hasn't acted on them.

    Deterministic and independent of the qa agent — runs even if rule
    synthesis failed, since the user's rejection is ground truth either way.
    """
    wd = _work_dir(run_id)
    try:
        fb = json.loads((wd / "feedback-input.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    dropped = 0
    for r in fb.get("feedback_rows", []):
        pid = r.get("page_id")
        text = (r.get("feedback") or "").strip()
        if (pid and (r.get("match_quality") or "OK") == "Feedback"
                and text and not text.startswith("[Auto]")):
            try:
                update_status(pid, DROPPED_FEEDBACK_STATUS)
                dropped += 1
            except Exception as e:
                print(f"  warn: could not archive {pid}: {e}")
    return dropped


def apply(run_id: str) -> dict:
    """Archive user-rejected rows, then write refined learned_* to Profile."""
    wd = _work_dir(run_id)

    # Archive user-rejected rows first — deterministic, independent of the qa
    # agent's success, so it happens even when rule synthesis failed.
    dropped = _drop_user_rejected(run_id)

    out_path = wd / "qa-output.json"
    try:
        data = json.loads(out_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"  warn: qa-output missing or malformed ({e}); "
              f"archived {dropped} user-rejected row(s), Profile unchanged")
        return {"updated": False, "dropped": dropped, "reason": str(e)}

    learned_exclusions = (data.get("learned_exclusions") or "").strip()
    learned_examples   = (data.get("learned_examples")   or "").strip()

    profile_update(
        learned_exclusions=learned_exclusions,
        learned_examples=learned_examples,
    )
    rationale = data.get("rationale", "")
    print(f"apply: updated Profile.learned_*; archived {dropped} user-rejected "
          f"row(s). Rationale: {rationale[:200]}")
    return {"updated": True, "rationale": rationale, "dropped": dropped}


_COMMANDS = {"prepare": prepare, "apply": apply}


def _main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"Usage: python3 -m recycle_feedback <command> [run_id]\n"
              f"Commands: {', '.join(_COMMANDS)}", file=sys.stderr)
        return 1
    cmd = sys.argv[1]
    run_id = sys.argv[2] if len(sys.argv) > 2 else uuid.uuid4().hex
    print(f"=== recycle_feedback.{cmd} · run_id={run_id[:8]} ===")
    _COMMANDS[cmd](run_id)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
