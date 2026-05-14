"""Stub Notify for Phase 2 — prints fire summary to console.

Phase 3 replaces with: summarize agent → Runs DB write → webhook POST.
"""
from __future__ import annotations


def send(new_rows: list[dict], fire_metrics: dict, run_id: str) -> None:
    """Print a human-readable summary of the fire.

    No Notion writes, no webhook POSTs in Phase 2.
    """
    pursue   = fire_metrics.get("pursue_count", 0)
    consider = fire_metrics.get("consider_count", 0)
    skim     = fire_metrics.get("skim_count", 0)
    pre_counts = fire_metrics.get("prefilter_counts", {})

    print(f"\n=== Fire complete · run_id={run_id[:8]} ===")
    print(f"  Discovery raw:       {fire_metrics.get('discovery_total', 0)}")
    print(f"  Cross-source dups:   {fire_metrics.get('cross_source_duplicates', 0)}")
    print(f"  After dedup:         {fire_metrics.get('after_dedup', 0)}")
    print(f"  After tracker dedup: {fire_metrics.get('after_tracker_check', 0)}")
    print(f"  S2 work mode drops:  {pre_counts.get('s2_work_mode', 0)}")
    print(f"  S3 country drops:    {pre_counts.get('s3_country_relocation', 0)}")
    print(f"  S4 seniority drops:  {pre_counts.get('s4_seniority', 0)}")
    print(f"  S7 company drops:    {pre_counts.get('s7_company_blacklist', 0)}")
    print(f"  S8a industry drops:  {pre_counts.get('s8a_industry_blacklist', 0)}")
    print(f"  S9 salary drops:     {pre_counts.get('s9_salary_floor', 0)}")
    print(f"  After prefilter:     {fire_metrics.get('after_prefilter', 0)}")
    print(f"  Pursue:              {pursue}")
    print(f"  Consider:            {consider}")
    print(f"  Skim:                {skim}")
    print(f"  Written to tracker:  {fire_metrics.get('written', 0)}")
    print(f"  Failed writes:       {fire_metrics.get('failed', 0)}")

    if fire_metrics.get("evaluation_stub"):
        print("\n  [Phase 2] Evaluation is stubbed — every survivor marked Strong.")
        print("  [Phase 3] Will add screener + scorer + Runs DB write + webhook.")
