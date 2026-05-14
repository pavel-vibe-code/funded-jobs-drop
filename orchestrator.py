"""Orchestrator — single-fire pipeline entry point.

Wires Discovery → Evaluation → State.write → Notify.

Phase 2 ships with stub Evaluation + stub Notify. Phase 3 swaps in real LLM
scoring and Runs DB writes + webhook delivery.

Run via `/fd-run` slash command, or directly: `python3 -m orchestrator`.
"""
from __future__ import annotations

import os
import uuid

from discovery.runner import run as discovery_run
from evaluation.runner import run as evaluation_run
from notify.runner import send as notify_send
from state.profile import Profile, read as profile_read
from state.tracker import read_url_index, write_evaluated


def fire() -> None:
    """Run a single fire: Discovery → Evaluation → State write → Notify."""
    run_id = uuid.uuid4().hex
    print(f"=== /fd-run starting · run_id={run_id[:8]} ===")

    # 1. Load profile
    if os.environ.get("FD_DRY_RUN") == "1":
        profile = Profile(
            variant="EU", home_country="Czechia", home_city="Prague",
            work_modes=["Remote", "Hybrid"], accepted_seniority=["senior", "staff"],
            interest_description="dry-run test profile", profile_hash="dry-run-hash-0000",
        )
    else:
        profile = profile_read()

    # 2. Tracker URL index (dedup against past evaluations)
    tracker_index = read_url_index()
    tracker_urls = set(tracker_index.keys())
    print(f"Tracker known URLs: {len(tracker_urls)}")

    # 3. Discovery
    candidates, discovery_metrics = discovery_run(
        profile, tracker_urls, last_fire_at_epoch=None  # cold-start; missed-fire logic uses Runs DB last_successful
    )

    # 4. Evaluation (stub in Phase 2)
    verdicts, evaluation_metrics = evaluation_run(candidates, profile, run_id)

    # 5. State write
    write_result = write_evaluated(verdicts)

    # 6. Notify (stub in Phase 2)
    combined_metrics = {
        **discovery_metrics,
        **evaluation_metrics,
        **write_result,
        "run_id": run_id,
    }
    notify_send(verdicts, combined_metrics, run_id)


if __name__ == "__main__":
    fire()
