"""Makes sure an alert claiming to be from Vexa is actually from Vexa, using the signature Vexa
attaches to every webhook it sends (documented in docs/docs/webhooks.mdx)."""

from __future__ import annotations

from sales_cycle._signature import verify_hmac_timestamp_signature


class WebhookSignatureError(RuntimeError):
    pass


def verify_webhook_signature(
    *, secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None,
    max_skew_sec: float = 300.0,
) -> None:
    verify_hmac_timestamp_signature(
        secret=secret, timestamp=timestamp, signature=signature, body=body,
        basestring_fmt="{timestamp}.{body}", prefix="sha256=", now=now, max_skew_sec=max_skew_sec,
        error_cls=WebhookSignatureError,
        missing_secret_msg="webhook secret not configured",
        missing_timestamp_msg="missing/invalid X-Webhook-Timestamp",
    )
