"""Makes sure an alert claiming to be from Vexa is actually from Vexa, using the signature Vexa
attaches to every webhook it sends (documented in docs/docs/webhooks.mdx)."""

from __future__ import annotations

import hashlib
import hmac
import time


class WebhookSignatureError(RuntimeError):
    pass


def verify_webhook_signature(
    *, secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None,
    max_skew_sec: float = 300.0,
) -> None:
    if not secret:
        raise WebhookSignatureError("webhook secret not configured")
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as e:
        raise WebhookSignatureError("missing/invalid X-Webhook-Timestamp") from e
    if abs((now if now is not None else time.time()) - ts) > max_skew_sec:
        raise WebhookSignatureError("stale timestamp — possible replay")
    basestring = f"{timestamp}.{body.decode()}".encode()
    expected = "sha256=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise WebhookSignatureError("signature mismatch")
