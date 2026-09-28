import hashlib
import hmac
import time

import pytest

from sales_cycle.slack_verify import SlackSignatureError, verify_slack_signature

SECRET = "test-signing-secret"


def _sign(timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode()}".encode()
    return "v0=" + hmac.new(SECRET.encode(), basestring, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    now = time.time()
    ts = str(int(now))
    body = b'{"type":"url_verification"}'
    verify_slack_signature(
        signing_secret=SECRET, timestamp=ts, signature=_sign(ts, body), body=body, now=now,
    )  # must not raise


def test_wrong_signature_rejected():
    ts = str(int(time.time()))
    body = b"{}"
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(signing_secret=SECRET, timestamp=ts, signature="v0=deadbeef", body=body)


def test_stale_timestamp_rejected():
    old_ts = str(int(time.time()) - 60 * 10)
    body = b"{}"
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(
            signing_secret=SECRET, timestamp=old_ts, signature=_sign(old_ts, body), body=body,
        )


def test_missing_secret_rejected():
    ts = str(int(time.time()))
    body = b"{}"
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(signing_secret="", timestamp=ts, signature=_sign(ts, body), body=body)


def test_malformed_timestamp_rejected():
    with pytest.raises(SlackSignatureError):
        verify_slack_signature(signing_secret=SECRET, timestamp="not-a-number", signature="v0=x", body=b"{}")
