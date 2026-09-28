"""The one HMAC-signature-plus-replay-window check, shared by both `slack_verify.py` and
`webhook_verify.py` — Slack and Vexa sign their requests almost identically (an HMAC-SHA256 over a
timestamp+body string, with a version-ish prefix on the hex digest); only the exact string format
and prefix differ. Each caller keeps its own function name, error type, and message wording — this
just holds the one copy of the actual crypto + timestamp-freshness check underneath both.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_hmac_timestamp_signature(
    *, secret: str, timestamp: str, signature: str, body: bytes, basestring_fmt: str, prefix: str,
    error_cls: type[Exception], missing_secret_msg: str, missing_timestamp_msg: str,
    now: float | None = None, max_skew_sec: float = 300.0,
) -> None:
    if not secret:
        raise error_cls(missing_secret_msg)
    try:
        ts = int(timestamp)
    except (TypeError, ValueError) as e:
        raise error_cls(missing_timestamp_msg) from e
    if abs((now if now is not None else time.time()) - ts) > max_skew_sec:
        raise error_cls("stale timestamp — possible replay")
    basestring = basestring_fmt.format(timestamp=timestamp, body=body.decode()).encode()
    expected = prefix + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature or ""):
        raise error_cls("signature mismatch")
