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

    @property
    def bot_token(self) -> str:
        return self._token

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

    def conversations_info(self, *, channel: str) -> dict:
        """Live-checks the configured channel — is it real, and is the bot actually a member?
        Returns Slack's own channel object (notably ``is_member``). For a PRIVATE channel the bot
        isn't in, Slack returns `channel_not_found` (private channels are invisible to a non-member
        entirely, same failure shape as a typo'd ID); for a PUBLIC one it returns real channel info
        with `is_member: false` instead — the caller (the Settings page's live connection check)
        treats both as "not usable yet", just with a different reason."""
        if not self._token:
            raise SlackError("SLACK_BOT_TOKEN not configured")
        resp = call(
            "GET", f"{self._base_url}/conversations.info",
            params={"channel": channel},
            headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
            error_cls=SlackError, error_prefix="conversations.info",
        )
        body = resp.json()
        if not body.get("ok"):
            code = body.get("error")
            raise SlackError(f"conversations.info rejected: {code}", error_code=code)
        return body.get("channel") or {}

    # ── voting on a feature request: reactions, thread replies, who is who ──

    def _api(self, slack_method: str, *, http_method: str = "POST", json: dict | None = None, params: dict | None = None) -> dict:
        """One Slack Web API call; returns Slack's body when ``ok`` and raises ``SlackError`` (with Slack's own error code) when not."""
        if not self._token:
            raise SlackError("SLACK_BOT_TOKEN not configured")
        resp = call(
            http_method, f"{self._base_url}/{slack_method}", json=json, params=params,
            headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
            error_cls=SlackError, error_prefix=slack_method,
        )
        body = resp.json()
        if not body.get("ok"):
            code = body.get("error")
            raise SlackError(f"{slack_method} rejected: {code}", error_code=code)
        return body

    def auth_test(self) -> dict:
        """Who this bot token is — notably ``user_id``, the id its own reactions carry (they are not votes)."""
        return self._api("auth.test")

    def reactions_add(self, *, channel: str, ts: str, name: str) -> None:
        """Puts a reaction on a message as the bot. Already being there is not an error."""
        try:
            self._api("reactions.add", json={"channel": channel, "timestamp": ts, "name": name})
        except SlackError as e:
            if e.error_code != "already_reacted":
                raise

    def reactions_remove(self, *, channel: str, ts: str, name: str) -> None:
        """Takes the bot's own reaction off a message. Its not being there is not an error."""
        try:
            self._api("reactions.remove", json={"channel": channel, "timestamp": ts, "name": name})
        except SlackError as e:
            if e.error_code != "no_reaction":
                raise

    def delete_message(self, *, channel: str, ts: str) -> None:
        """Deletes a message the bot itself posted."""
        self._api("chat.delete", json={"channel": channel, "ts": ts})

    def reactions_get(self, *, channel: str, ts: str) -> dict[str, list[str]]:
        """Every reaction on a message and who used it, straight from Slack (``full``: the whole user list, not a sample)."""
        body = self._api("reactions.get", http_method="GET", params={"channel": channel, "timestamp": ts, "full": "true"})
        return {r["name"]: list(r.get("users") or []) for r in (body.get("message") or {}).get("reactions") or []}

    def post_thread_reply(self, *, channel: str, thread_ts: str, text: str) -> str:
        return self._api("chat.postMessage", json={"channel": channel, "thread_ts": thread_ts, "text": text})["ts"]

    def users_info(self, *, user: str) -> dict:
        return self._api("users.info", http_method="GET", params={"user": user}).get("user") or {}

    def usergroup_members(self, *, usergroup: str) -> list[str]:
        return list(self._api("usergroups.users.list", http_method="GET", params={"usergroup": usergroup}).get("users") or [])
