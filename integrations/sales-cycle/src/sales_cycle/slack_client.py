"""Plain-HTTP Slack client — Bot API (chat.postMessage), not an incoming webhook, because we need the
message `ts` back to correlate a later reaction against it. No SDK (Category-A licensing stance)."""

from __future__ import annotations

from sales_cycle._http import call


class SlackError(RuntimeError):
    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        # Slack's own machine-readable `error` field from a rejected chat.postMessage response
        # (e.g. "not_in_channel", "channel_not_found") — None for a transport-level failure (no
        # response body to read a code from). A caller that wants an ACTIONABLE message for a
        # specific, known cause branches on this instead of parsing the exception string.
        self.error_code = error_code


class SlackClient:
    def __init__(self, *, bot_token: str, base_url: str = "https://slack.com/api", timeout: float = 5.0):
        self._token = bot_token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def post_message(self, *, channel: str, text: str) -> str:
        """Returns the message `ts` (Slack's timestamp-as-id) — the correlation key for the reaction."""
        if not self._token:
            raise SlackError("SLACK_BOT_TOKEN not configured")
        resp = call(
            "POST", f"{self._base_url}/chat.postMessage",
            json={"channel": channel, "text": text},
            headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
            error_cls=SlackError, error_prefix="chat.postMessage",
        )
        body = resp.json()
        if not body.get("ok"):
            code = body.get("error")
            raise SlackError(f"chat.postMessage rejected: {code}", error_code=code)
        return body["ts"]
