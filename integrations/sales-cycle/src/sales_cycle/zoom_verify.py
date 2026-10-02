"""Proves a notification really came from Zoom, and answers the endpoint-validation handshake.

Zoom signs each notification: HMAC-SHA256 of ``v0:{timestamp}:{raw body}`` keyed with the app's secret token,
hex-encoded and prefixed ``v0=`` — the same shape as Slack's, so the check is the shared one. This endpoint is
reachable from the internet; without it anyone could forge a "meeting started" and send the bot anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from sales_cycle._signature import verify_hmac_timestamp_signature


class ZoomSignatureError(RuntimeError):
    pass


def verify_zoom_signature(
    *, secret_token: str, timestamp: str, signature: str, body: bytes, now: float | None = None,
) -> None:
    verify_hmac_timestamp_signature(
        secret=secret_token, timestamp=timestamp, signature=signature, body=body,
        basestring_fmt="v0:{timestamp}:{body}", prefix="v0=", now=now, max_skew_sec=300.0,
        error_cls=ZoomSignatureError,
        missing_secret_msg="ZOOM_WEBHOOK_SECRET_TOKEN not configured",
        missing_timestamp_msg="missing/invalid x-zm-request-timestamp",
    )


# The handshake signs whatever token it is handed, so it is an HMAC oracle for the same key that signs
# notifications: handed ``v0:{ts}:{forged body}`` it would return a valid signature for a forged event. Zoom's
# plain tokens are short random strings; anything with the punctuation the signed string needs is refused.
_PLAIN_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def url_validation_response(*, secret_token: str, plain_token: str) -> dict:
    if not secret_token:
        raise ZoomSignatureError("ZOOM_WEBHOOK_SECRET_TOKEN not configured")
    if not _PLAIN_TOKEN.match(plain_token or ""):
        raise ZoomSignatureError("malformed plainToken")
    digest = hmac.new(secret_token.encode(), plain_token.encode(), hashlib.sha256).hexdigest()
    return {"plainToken": plain_token, "encryptedToken": digest}
