"""Makes sure a request claiming to be from Slack is actually from Slack (Slack's own recommended
check: https://api.slack.com/authentication/verifying-requests-from-slack). This endpoint is
reachable from the public internet — without this check, anyone could fake a Slack approval and
trigger a real branch/push."""

from __future__ import annotations

from sales_cycle._signature import verify_hmac_timestamp_signature


class SlackSignatureError(RuntimeError):
    pass


def verify_slack_signature(
    *, signing_secret: str, timestamp: str, signature: str, body: bytes, now: float | None = None,
) -> None:
    verify_hmac_timestamp_signature(
        secret=signing_secret, timestamp=timestamp, signature=signature, body=body,
        basestring_fmt="v0:{timestamp}:{body}", prefix="v0=", now=now, max_skew_sec=300.0,
        error_cls=SlackSignatureError,
        missing_secret_msg="SLACK_SIGNING_SECRET not configured",
        missing_timestamp_msg="missing/invalid X-Slack-Request-Timestamp",
    )
