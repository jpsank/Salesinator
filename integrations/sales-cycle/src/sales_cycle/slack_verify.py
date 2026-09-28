"""Verify a Slack Events API request is genuinely from Slack (HMAC signature + replay window) —
https://api.slack.com/authentication/verifying-requests-from-slack. This is a public webhook endpoint;
skipping this check would let anyone POST fake approvals that trigger a real branch/push later."""

from __future__ import annotations

import hashlib
import hmac
import time


class SlackSignatureError(RuntimeError):
    pass


def verify_slack_signature(
    *, signing_secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None,
) -> None:
    if not signing_secret:
        raise SlackSignatureError("SLACK_SIGNING_SECRET not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as e:
        raise SlackSignatureError("missing/invalid X-Slack-Request-Timestamp") from e
    if abs((now if now is not None else time.time()) - ts) > 60 * 5:
        raise SlackSignatureError("stale timestamp — possible replay")
    basestring = f"v0:{timestamp}:{body.decode()}".encode()
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise SlackSignatureError("signature mismatch")
