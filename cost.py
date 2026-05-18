"""Per-fire LLM cost estimation.

Claude Code runs the screener / scorer / summarize / qa agents as subagents.
Their token usage is not surfaced back to the Python orchestrator across the
skill -> agent boundary, so cost is *estimated* deterministically from the
files each agent reads and writes:

  /tmp/fd-run/<run_id>/      screener, scorer, summarize I/O
  /tmp/fd-recycle/<run_id>/  qa I/O

Method: count characters in each agent's input + output files, convert to
tokens via a fixed ratio, price with a static per-model table. No network, no
LLM, no harness dependency -- re-running on the same files yields the same
number (idempotent; deterministic-before-LLM, per CLAUDE.md pillar 2).

The estimate is intentionally conservative: input is priced at the uncached
rate, so prompt caching makes the real bill somewhat lower. Two knobs --
CHARS_PER_TOKEN and PER_CALL_OVERHEAD_TOKENS -- and the _PRICES table should
be calibrated once a real Anthropic invoice exists for a few fires. Compare
the per-agent token counts this module reports against the Console usage for
the same run, then adjust.
"""
from __future__ import annotations

from pathlib import Path


# ── Calibration knobs ─────────────────────────────────────────────────

# Average characters per token for mixed JSON + English. Claude's tokenizer
# runs ~3.5-4.0; calibrate against a measured run.
CHARS_PER_TOKEN = 3.8

# Per-call input tokens NOT present in the input file: the dispatch prompt
# the /fd-run skill sends ("Read <path> and produce ...") plus an averaged
# allowance for the Claude Code subagent harness system prompt. The harness
# prompt is large but largely cached across a fire's many calls, so this is a
# rough blended figure -- the single biggest thing to calibrate.
PER_CALL_OVERHEAD_TOKENS = 1500


# ── Model price table -- USD per 1,000,000 tokens ─────────────────────
# VERIFY against current Anthropic pricing before trusting dollar figures.
# Keys match the `model:` field in .claude/agents/*.md.
PRICE_TABLE_VERSION = "2026-05-18"
_PRICES = {
    "haiku":  {"input": 1.00, "output": 5.00},   # Haiku 4.5
    "sonnet": {"input": 3.00, "output": 15.00},  # Sonnet 4.6
    "opus":   {"input": 5.00, "output": 25.00},  # Opus 4.7
}


# ── Agent call inventory ──────────────────────────────────────────────
# Each tuple: (agent, input filename glob, output filename glob). One input
# file == one agent dispatch. The screener has two patterns (VC batches and
# post-JD favorites); qa runs in /tmp/fd-recycle/ rather than /tmp/fd-run/.
_RUN_CALLS = [
    ("screener",  "candidates-batch-*.json",      "screener-verdicts-*.json"),
    ("screener",  "favorites-postjd-batch-*.json", "postjd-verdicts-*.json"),
    ("scorer",    "scorer-input-*.json",           "scorer-output-*.json"),
    ("summarize", "summarize-input.json",          "summary.json"),
]
_RECYCLE_CALLS = [
    ("qa", "feedback-input.json", "qa-output.json"),
]

_AGENTS_DIR = Path(__file__).resolve().parent / ".claude" / "agents"


def _tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def _file_chars(path: Path) -> int:
    try:
        return len(path.read_text())
    except OSError:
        return 0


def _agent_model(agent: str) -> str:
    """The `model:` value from the agent's frontmatter (haiku/sonnet/opus)."""
    try:
        for line in (_AGENTS_DIR / f"{agent}.md").read_text().splitlines():
            s = line.strip()
            if s.startswith("model:"):
                return s.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _agent_prompt_tokens(agent: str) -> int:
    """Tokens for the agent definition, prepended as the system prompt on
    every dispatch of that agent."""
    return _tokens(_file_chars(_AGENTS_DIR / f"{agent}.md"))


def _price(model: str, input_tokens: int, output_tokens: int) -> float:
    p = _PRICES.get(model)
    if not p:
        return 0.0
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def _scan(agent: str, in_glob: str, out_glob: str, work_dir: Path) -> dict | None:
    """Estimate one (agent, file-pattern) call group, or None if it didn't run."""
    in_files = sorted(work_dir.glob(in_glob))
    if not in_files:
        return None
    out_files = sorted(work_dir.glob(out_glob))
    calls = len(in_files)
    per_call_sys = _agent_prompt_tokens(agent) + PER_CALL_OVERHEAD_TOKENS

    input_tokens = per_call_sys * calls + sum(_tokens(_file_chars(f)) for f in in_files)
    output_tokens = sum(_tokens(_file_chars(f)) for f in out_files)
    model = _agent_model(agent)
    return {
        "agent":         agent,
        "model":         model,
        "calls":         calls,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      _price(model, input_tokens, output_tokens),
    }


def estimate_run_cost(run_id: str) -> dict:
    """Estimate the LLM cost of one fire from its /tmp work files.

    Deterministic and idempotent -- same files in, same number out. Safe to
    call at finalize; missing files (skipped stages) contribute nothing.
    Retried agent dispatches are undercounted (one input file == one call),
    which is rare and minor.

    Returns:
      {
        "cost_usd": float,                 # total, all agents
        "by_agent": {agent: {model, calls, input_tokens,
                             output_tokens, cost_usd}},
        "price_table_version": str,
        "estimated": True,
      }
    """
    run_dir = Path("/tmp/fd-run") / run_id
    recycle_dir = Path("/tmp/fd-recycle") / run_id

    groups = [g for agent, ig, og in _RUN_CALLS
              if (g := _scan(agent, ig, og, run_dir)) is not None]
    groups += [g for agent, ig, og in _RECYCLE_CALLS
               if (g := _scan(agent, ig, og, recycle_dir)) is not None]

    # The screener has two file patterns -- merge groups sharing an agent.
    by_agent: dict[str, dict] = {}
    for g in groups:
        a = by_agent.get(g["agent"])
        if a is None:
            by_agent[g["agent"]] = {
                "model":         g["model"],
                "calls":         g["calls"],
                "input_tokens":  g["input_tokens"],
                "output_tokens": g["output_tokens"],
                "cost_usd":      round(g["cost_usd"], 4),
            }
        else:
            a["calls"] += g["calls"]
            a["input_tokens"] += g["input_tokens"]
            a["output_tokens"] += g["output_tokens"]
            a["cost_usd"] = round(a["cost_usd"] + g["cost_usd"], 4)

    total = round(sum(a["cost_usd"] for a in by_agent.values()), 4)
    return {
        "cost_usd":             total,
        "by_agent":             by_agent,
        "price_table_version":  PRICE_TABLE_VERSION,
        "estimated":            True,
    }
