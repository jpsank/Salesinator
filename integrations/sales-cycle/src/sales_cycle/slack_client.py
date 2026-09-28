"""Plain-HTTP Slack client — Bot API (chat.postMessage), not an incoming webhook, because we need the
message `ts` back to correlate a later reaction against it. No SDK (Category-A licensing stance)."""

from __future__ import annotations

from sales_cycle._http import call


class SlackError(RuntimeError):
    pass


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
            raise SlackError(f"chat.postMessage rejected: {body.get('error')}")
        return body["ts"]
