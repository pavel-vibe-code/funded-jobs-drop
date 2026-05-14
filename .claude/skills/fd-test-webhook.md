---
name: fd-test-webhook
description: Send a single test message to the webhook URL configured in the user's Profile. Confirms Slack/Discord/Teams/Zapier wiring before /fd-run depends on it.
---

# /fd-test-webhook — probe the configured webhook

Manual command. Sends one test message to the URL stored in `Profile.webhook_url` and reports whether the POST succeeded.

## Run

```bash
python3 -m notify.webhook test
```

## Interpret the result

- **`OK: test message delivered to …`** — the webhook accepted the POST. Check the channel/destination to confirm the message rendered correctly there (Slack/Discord/etc.).
- **`FAILED: HTTP 4xx …`** — webhook URL rejected the request. Common causes: revoked Slack incoming-webhook URL, wrong Zapier hook secret, Discord webhook deleted. Re-create the webhook and update Profile via `/fd-settings`.
- **`FAILED: URL error: …`** — network or DNS issue. The URL may be malformed (typo, missing scheme) or unreachable from your machine.
- **`ERROR: no webhook_url configured in Profile`** — run `/fd-settings` first to set the URL.
- **`WARN: webhook_enabled is False`** — the test still sends, but `/fd-run` will silently skip posting until you flip the toggle in `/fd-settings`.

## Notes

- The probe always sends; it does not check `webhook_enabled` as a gate. That's deliberate — you want to verify URL wiring even while the feature is off.
- Slack reads the `text` field; Discord reads `content`. We send both, so the same payload works on either platform.
- Teams only accepts MessageCard-shaped payloads at the older webhook endpoint and won't render the test cleanly — it'll arrive but as a raw blob. Modern Teams webhooks via Workflows accept the same `text` payload.
