import hashlib
import hmac
import time

import pytest

from sales_cycle.webhook_verify import WebhookSignatureError, verify_webhook_signature

SECRET = "test-webhook-secret"


def _sign(timestamp: str, body: bytes) -> str:
    basestring = f"{timestamp}.{body.decode()}".encode()
    return "sha256=" + hmac.new(SECRET.encode(), basestring, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    now = time.time()
    ts = str(int(now))
    body = b'{"event_type":"meeting.started"}'
    verify_webhook_signature(secret=SECRET, timestamp=ts, signature=_sign(ts, body), body=body, now=now)


def test_wrong_signature_rejected():
    ts = str(int(time.time()))
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(secret=SECRET, timestamp=ts, signature="sha256=deadbeef", body=b"{}")


def test_stale_timestamp_rejected():
    old_ts = str(int(time.time()) - 600)
    body = b"{}"
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(secret=SECRET, timestamp=old_ts, signature=_sign(old_ts, body), body=body)


def test_missing_secret_rejected():
    ts = str(int(time.time()))
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(secret="", timestamp=ts, signature=_sign(ts, b"{}"), body=b"{}")


def test_malformed_timestamp_rejected():
    with pytest.raises(WebhookSignatureError):
        verify_webhook_signature(secret=SECRET, timestamp="nope", signature="sha256=x", body=b"{}")
