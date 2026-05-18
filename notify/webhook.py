"""Webhook notification — tool-agnostic POST to user's configured URL.

Works with Slack, Discord, Teams (limited), Zapier, n8n, or any incoming-webhook
endpoint that accepts JSON. Sends both `text` and `content` field names since
Slack reads `text` and Discord reads `content`; each ignores the other.

Failures are non-fatal — webhook POST errors are logged but don't break the fire.

When invoked as `python3 -m notify.webhook test`, reads the user's webhook
config from Profile and POSTs a one-line probe message. Used by /fd-test-webhook.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional


WEBHOOK_TIMEOUT_S = 10


def post_webhook(webhook_url: str, message_text: str) -> Optional[str]:
    """POST message_text to webhook_url. Returns None on success, error string on failure."""
    if os.environ.get("FD_DRY_RUN") == "1":
        return None

    payload = {"text": message_text, "content": message_text}
    req = urllib.request.Request(
        webhook_url,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL error: {e.reason}"
    except Exception as e:  # webhook errors must never break the fire
        return f"{type(e).__name__}: {e}"


def format_match_message(summary: str, verdicts: list[dict],
                         metrics: dict) -> str:
    """Compose one webhook message for a fire's notify-worthy matches.

    Pursue rows get a full block (location / salary / seniority + why_fits).
    Consider rows — present only when the user set webhook_notify_tier to
    "Decent — Consider" — get a compact one-liner, so a Consider-tier fire
    doesn't become a wall of text.

    Plain text with markdown that renders on Slack and Discord
    (bold via *...*, auto-linked URLs).
    """
    pursue   = [v for v in verdicts if v.get("match") == "Strong — Pursue"]
    consider = [v for v in verdicts if v.get("match") == "Decent — Consider"]
    variant = metrics.get("variant", "EU")
    today = metrics.get("started_at_iso", "")[:10]

    counted = []
    if pursue:
        counted.append(f"{len(pursue)} Pursue")
    if consider:
        counted.append(f"{len(consider)} Consider")
    header = f"*{' + '.join(counted)} new matches — {variant} run, {today}*"

    counts_line = (
        f"{metrics.get('total_new', 0)} new in tracker "
        f"({metrics.get('pursue_count', 0)} Pursue, "
        f"{metrics.get('consider_count', 0)} Consider, "
        f"{metrics.get('skim_count', 0)} Skim) · "
        f"cost ${metrics.get('cost_usd', 0):.2f}"
    )

    parts = [header, counts_line, "", summary, ""]

    for v in pursue:
        why = (v.get("why_fits") or "").strip()
        block = [
            f"*{v.get('title', '?')}* — {v.get('company', '?')}",
            f"📍 {v.get('location', '?')} · 💶 {v.get('salary', '—')} · "
            f"{v.get('seniority') or '—'}",
        ]
        if why:
            block.append(why)
        block.append(f"Apply: {v.get('canonical_url', '')}")
        block.append("")  # blank line between entries
        parts.extend(block)

    if consider:
        parts.append("*Consider — worth a look:*")
        for v in consider:
            parts.append(
                f"• *{v.get('title', '?')}* — {v.get('company', '?')} · "
                f"📍 {v.get('location', '?')} · {v.get('canonical_url', '')}"
            )
        parts.append("")

    return "\n".join(parts)


def _test_main() -> int:
    """Probe the user's configured webhook with a test message."""
    from state.profile import read as profile_read

    try:
        profile = profile_read()
    except Exception as e:
        print(f"ERROR: could not read Profile: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if not profile.webhook_url:
        print("ERROR: no webhook_url configured in Profile. "
              "Set it via /fd-settings before testing.", file=sys.stderr)
        return 3
    if not profile.webhook_enabled:
        print("WARN: webhook_enabled is False — /fd-run won't post to this URL. "
              "Sending test message anyway.", file=sys.stderr)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    message = (
        f"*Funded Drop · webhook test* — {now_iso}\n"
        f"If you see this in your channel, the webhook is wired correctly. "
        f"`/fd-run` will post here when new Pursue matches arrive."
    )
    err = post_webhook(profile.webhook_url, message)
    if err is None:
        print(f"OK: test message delivered to {profile.webhook_url[:60]}…")
        return 0
    print(f"FAILED: {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "test":
        sys.exit(_test_main())
    print("Usage: python3 -m notify.webhook test", file=sys.stderr)
    sys.exit(1)
