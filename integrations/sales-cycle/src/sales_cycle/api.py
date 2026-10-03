"""Every web address (endpoint) this add-on exposes, and what each one is for.

POST /dispatch
    Send a bot into a call, same as Vexa's own POST /bots, but also accepts a customer name
    (`customer_tag`) and tags the call with that customer before the bot joins.

POST /tag
    Tag a call that's already underway (or already sent) with a customer name — this is what the
    Slack `/vexa-tag` command calls.

POST /slack/events
    Slack sends things here: the one-time "prove you own this URL" handshake when you first set
    this up, and later, every time someone reacts to a message with an emoji.

POST /internal/process-approved
    For every approved feature request: if it hasn't been started yet, start the AI agent building
    it; if it's already building, check whether it's done, and if so, push it to GitHub; if it's
    already pushed, open a pull request for it. Also meant to run on a schedule.

POST /webhooks/meeting-started
    Vexa calls this the moment a bot joins a call. Used for the automatic version of customer
    tagging (see calendar_resolver.py, falling back to settings.fallback_workspace_id on a miss so
    a call is never left permanently untagged), turning on Vexa's live meeting copilot for the call
    (settings.auto_process_calls — on by default, since inviting the bot is itself the consent
    signal), and starting this call's live feature-request watcher (see live_card_watcher.py) — the
    thing that posts each feature request to Slack the moment the copilot surfaces it, not after the
    call ends.

GET /oauth/{hubspot,slack}/authorize, .../callback, .../status, POST .../disconnect
    Each provider's whole "Connect X" flow — see oauth_routes.py (the shared 4-route shape),
    hubspot_oauth.py and slack_oauth.py (what's actually provider-specific). `authorize` sends the
    browser to the provider's consent screen; `callback` is where it sends the browser back with a
    code; `status`/`disconnect` back the Settings page's "Connected as ___ / Disconnect" display.

GET /slack/channel-status
    Live-checks whether the connected Slack app can actually post to the configured channel
    (connected ≠ invited — see the handler's own docstring).

GET /slack/channel, POST /slack/channel
    The effective "which channel do feature-request cards post to" value and where it came from
    (a Settings-page override vs. the SALES_CYCLE_SLACK_CHANNEL_ID env default), and how to set or
    clear that override — the UI alternative to editing the env var and restarting the service.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from sales_cycle import hubspot_oauth, internal_auth, slack_oauth
from sales_cycle.approval_policy import APPROVE_REACTIONS, base_reaction, decide, is_vote_reaction, tally
from sales_cycle.approvers import ApproverPolicy, InvalidPolicy, LeaderResolver, load_policy, save_policy
from sales_cycle.calendar_resolver import resolve_meeting_started
from sales_cycle.github_checks import (
    GitHubError, fetch_changed_files, fetch_check_runs, fetch_head_sha, fetch_workspace_globs, ignored_checks, parse_pr_url, uncovered_by_ci, verdict,
)
from sales_cycle.hubspot_client import HubSpotClient
from sales_cycle.live_card_watcher import watch_meeting
from sales_cycle.oauth_routes import OAuthProviderConfig, register_oauth_routes
from sales_cycle.orchestrator import (
    DispatchError, PullRequestError, PushError,
    git_state, is_auth_failure, open_pull_request, push_if_ready, submit_implementation,
)
from sales_cycle.resolver import (
    WorkspaceBindError, bind_meeting_workspace, enable_copilot_processing, resolve_by_tag, slug_for_company,
)
from sales_cycle.settings import Settings, get_settings
from sales_cycle.slack_client import SlackClient, SlackError
from sales_cycle.slack_verify import SlackSignatureError, verify_slack_signature
from sales_cycle.store import PendingApproval, Store
from sales_cycle.webhook_verify import WebhookSignatureError, verify_webhook_signature
from sales_cycle.zoom_routes import register_zoom_routes

logger = logging.getLogger("sales_cycle.api")

app = FastAPI(title="vexa-sales-cycle")
internal_auth.install(app)        # everything not listed as public there needs X-Internal-Secret

_store: Store | None = None

# Strong references to live watch_meeting() tasks, keyed by meeting_id — asyncio only holds a weak
# reference to a fire-and-forget task, so without this a task can be garbage-collected mid-run.
# Popped by its own done-callback once the task exits (meeting end OR a caught internal error —
# watch_meeting never raises, so this alone can't tell the two apart; see sweep_live_watchers, which
# is what actually decides whether a missing task means "done" or "needs restarting"). This dict
# does NOT survive a process restart — active_watchers in the store is the durable record that does;
# the sweep reconciles the two.
_watch_meeting_tasks: dict[str, asyncio.Task] = {}


def _start_watcher(*, agent_api_url: str, meeting_api_url: str, subject: str, meeting_id: str) -> None:
    """Persist that this meeting should have a live watcher (survives a restart), then actually
    start one — unless one is already tracked and still running, in which case this is a no-op.

    The guard lives HERE, not in each caller: it used to be each caller's own job (a fresh
    meeting_id's check vs. sweep_live_watchers' liveness check), and the webhook handler was the one
    caller that didn't have one. Reproduced live: Vexa delivered meeting.started twice for the same
    meeting, both calls raced the same copilot SSE feed, and NEITHER posted a single card for the
    rest of the meeting. One shared check here covers every current and future caller — a caller
    forgetting to guard is no longer a way to reintroduce that bug."""
    if meeting_id in _watch_meeting_tasks and not _watch_meeting_tasks[meeting_id].done():
        return
    settings = get_settings()
    store = get_store()
    store.record_watcher_started(meeting_id, subject)
    task = asyncio.create_task(watch_meeting(
        agent_api_url=agent_api_url, meeting_api_url=meeting_api_url,
        subject=subject, meeting_id=meeting_id, store=store, slack=_slack(),
        channel=_slack_channel_id(), unmapped_slug=settings.unmapped_workspace_slug,
    ))
    _watch_meeting_tasks[meeting_id] = task
    task.add_done_callback(lambda finished, _mid=meeting_id: _watcher_task_finished(_mid, finished))


def _watcher_task_finished(meeting_id: str, finished: asyncio.Task) -> None:
    """A watcher task's done-callback. Pops the tracked entry only if `finished` is STILL the
    tracked task for this meeting_id — a done-callback fires asynchronously, so a crash or a fresh
    restart can register a NEWER task for the same meeting_id before this (older, now-finished)
    task's own callback gets scheduled. Popping unconditionally by key would delete the new task's
    live entry out from under it, and the next sweep/webhook, finding meeting_id gone from the dict,
    would start a THIRD task racing the second — the exact "two watchers, neither posts" bug
    _start_watcher's own guard exists to prevent, reintroduced through the cleanup path instead of
    the start path."""
    if _watch_meeting_tasks.get(meeting_id) is finished:
        _watch_meeting_tasks.pop(meeting_id, None)


# Mirrors meeting-api's own _RUNNING_STATUSES (collector/app.py) — a meeting in any of these is
# still genuinely live and worth watching; anything else (completed, failed, stopped, …) is done.
_LIVE_MEETING_STATUSES = frozenset({"requested", "joining", "awaiting_admission", "active", "stopping"})


@app.post("/internal/sweep-live-watchers")
async def sweep_live_watchers() -> dict:
    """Self-heals the ONE gap watch_meeting's own docstring already named: nothing notices or
    restarts a watcher lost to a sales-cycle restart mid-call. Reproduced live: exactly that,
    tonight — a real feature_request card was correctly tagged, but the watcher for that meeting
    had died with the previous process and nothing ever posted it.

    active_watchers (the store) is the durable record of "this meeting should have one"; it survives
    the restart that kills _watch_meeting_tasks (in-process only). For each row: if a live task is
    already running for it, nothing to do. Otherwise, ask meeting-api whether the meeting is still
    genuinely live — if so, restart the watcher (a fresh SSE connection from wherever the copilot
    stream currently is; anything posted before the gap is simply missed, not retried, same as any
    other live-only capture); if the meeting has reached a terminal status (or no longer exists),
    just clean up the row. A query failure is treated as "assume still live, try again next sweep" —
    the cost of an unnecessary restart attempt is far lower than the cost of abandoning a real,
    still-live meeting's watcher over a transient meeting-api blip."""
    settings = get_settings()
    restarted: list[str] = []
    stopped: list[str] = []
    async with httpx.AsyncClient() as client:
        for meeting_id, subject in get_store().list_active_watchers():
            if meeting_id in _watch_meeting_tasks and not _watch_meeting_tasks[meeting_id].done():
                continue  # already being watched — nothing to do
            still_live = True
            try:
                r = await client.get(
                    f"{settings.meeting_api_internal_url.rstrip('/')}/meetings/{meeting_id}",
                    headers={"X-User-Id": subject}, timeout=5.0,
                )
                if r.status_code == 404:
                    still_live = False
                elif r.status_code == 200:
                    still_live = r.json().get("status") in _LIVE_MEETING_STATUSES
                # any other status: leave still_live=True — assume still live, try again next sweep
            except httpx.HTTPError:
                pass  # transport failure — same "assume still live" fallback as above
            if still_live:
                _start_watcher(
                    agent_api_url=settings.agent_api_internal_url,
                    meeting_api_url=settings.meeting_api_internal_url,
                    subject=subject, meeting_id=meeting_id,
                )
                restarted.append(meeting_id)
                logger.warning(
                    "restarted a missing live card watcher for meeting_id=%s — its previous watcher "
                    "was lost (a service restart mid-call) while the meeting was still live",
                    meeting_id,
                )
            else:
                get_store().record_watcher_stopped(meeting_id)
                stopped.append(meeting_id)
    return {"restarted": restarted, "stopped": stopped}


def get_store() -> Store:
    """Lazy singleton so tests can point SALES_CYCLE_DB_PATH at a temp file before first use."""
    global _store
    if _store is None:
        _store = Store(get_settings().db_path)
    return _store


class DispatchRequest(BaseModel):
    platform: str | None = None
    native_meeting_id: str | None = None
    meeting_url: str | None = None
    bot_name: str | None = None
    language: str | None = None
    task: str | None = None
    transcribe_enabled: bool | None = None
    recording_enabled: bool | None = None
    customer_tag: str | None = None


class TagRequest(BaseModel):
    platform: str
    native_meeting_id: str
    customer_tag: str


class TagResponse(BaseModel):
    workspace_id: str
    company_name: str


def _hubspot() -> HubSpotClient:
    """Prefers a real "Connect HubSpot" OAuth connection; falls back to a hand-set static token
    (`SALES_CYCLE_HUBSPOT_TOKEN`) if HubSpot was never connected that way — so an operator who just
    wants to test this with their own private-app token doesn't need to go through OAuth at all."""
    s = get_settings()
    oauth_token = hubspot_oauth.get_valid_access_token(
        store=get_store(), client_id=s.hubspot_oauth_client_id, client_secret=s.hubspot_oauth_client_secret,
    )
    return HubSpotClient(token=oauth_token or s.hubspot_token, base_url=s.hubspot_base_url)


def _slack() -> SlackClient:
    """Same pick-one as HubSpot: prefers "Connect Slack", falls back to a hand-set bot token."""
    s = get_settings()
    oauth_token = slack_oauth.get_access_token(store=get_store())
    return SlackClient(bot_token=oauth_token or s.slack_bot_token)


def _slack_channel_id() -> str:
    """Which channel feature-request cards actually post to, right now. A Settings-page override
    (set via POST /slack/channel) wins over SALES_CYCLE_SLACK_CHANNEL_ID — same override-wins-over-
    env-default shape as _slack()'s OAuth-token-over-static-token. Every caller that needs "the
    configured channel" (the watcher that posts cards, the status check below) goes through this,
    not settings.slack_channel_id directly, so a channel switched from the Settings page actually
    takes effect instead of only changing what the status card displays."""
    override = get_store().get_runtime_setting("slack_channel_id")
    return override or get_settings().slack_channel_id


class SlackChannelStatus(BaseModel):
    configured: bool
    channel_id: str | None = None
    channel_name: str | None = None
    is_member: bool | None = None
    error: str | None = None


@app.get("/slack/channel-status", response_model=SlackChannelStatus)
async def slack_channel_status() -> SlackChannelStatus:
    """Live-checks whether the connected Slack app can ACTUALLY post feature-request cards to the
    configured channel — not just whether OAuth succeeded. A connected-but-never-invited app looks
    identical to a working one from OAuth status alone (reproduced live: a real feature_request card
    was tagged from a real call and silently never reached Slack, for exactly this reason). Rendered
    on the Settings → Integrations page's Slack card, alongside the OAuth connect/disconnect state,
    so the failure surfaces at SETUP time — before any card is ever lost, not after.

    conversations_info is a real, synchronous Slack API call — run_in_threadpool so a slow/stuck
    Slack response doesn't block this whole process's event loop for every other concurrent request."""
    channel_id = _slack_channel_id()
    if not channel_id:
        return SlackChannelStatus(configured=False)
    try:
        channel = await run_in_threadpool(_slack().conversations_info, channel=channel_id)
    except SlackError as exc:
        return SlackChannelStatus(
            configured=True, channel_id=channel_id,
            error=exc.error_code or str(exc),
        )
    return SlackChannelStatus(
        configured=True, channel_id=channel_id,
        channel_name=channel.get("name"), is_member=channel.get("is_member"),
    )


class SlackChannelConfig(BaseModel):
    channel_id: str | None = None
    # "override" = set from the Settings page; "env" = SALES_CYCLE_SLACK_CHANNEL_ID; "unset" =
    # neither — no channel configured at all yet.
    source: str


class SetSlackChannelBody(BaseModel):
    model_config = {"extra": "forbid"}
    channel_id: str


def _slack_channel_config() -> SlackChannelConfig:
    override = get_store().get_runtime_setting("slack_channel_id")
    env_default = get_settings().slack_channel_id
    channel_id = override or env_default
    source = "override" if override else ("env" if env_default else "unset")
    return SlackChannelConfig(channel_id=channel_id or None, source=source)


@app.get("/slack/channel", response_model=SlackChannelConfig)
async def get_slack_channel() -> SlackChannelConfig:
    """What the Settings page's Slack card shows in its "Channel ID" field — the EFFECTIVE value
    (a Settings-page override if one's been saved, else the env-var default), with `source` so the
    UI can tell an operator "this is from your env var" vs. "this is what you set here"."""
    return _slack_channel_config()


@app.post("/slack/channel", response_model=SlackChannelConfig)
async def set_slack_channel(body: SetSlackChannelBody) -> SlackChannelConfig:
    """Sets (or, with an empty string, clears) the Settings-page override for which channel
    feature-request cards post to — the UI alternative to SALES_CYCLE_SLACK_CHANNEL_ID + a restart.
    Clearing reverts to the env default, not to nothing (set_runtime_setting's own contract)."""
    get_store().set_runtime_setting("slack_channel_id", body.channel_id.strip())
    return _slack_channel_config()


class SlackEventsStatus(BaseModel):
    last_event_at: float | None = None          # the last verified event Slack delivered (unix seconds)
    last_rejected_at: float | None = None       # the last request that said it was from Slack but failed its signature check


def _runtime_float(key: str) -> float | None:
    raw = get_store().get_runtime_setting(key)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


@app.get("/slack/events-status", response_model=SlackEventsStatus)
async def slack_events_status() -> SlackEventsStatus:
    """When Slack last sent this service an event. Only a hint: a quiet channel and a broken connection look the same here — the check
    below tells them apart."""
    return SlackEventsStatus(last_event_at=_runtime_float("slack_last_event_at"), last_rejected_at=_runtime_float("slack_last_rejected_at"))


class SlackEventsCheck(BaseModel):
    delivered: bool
    refused: bool = False                       # Slack's request reached us but its signature failed
    target: str | None = None                   # what the bot reacted to
    detail: str


_EVENTS_CHECK_WAIT_S = 10.0


@app.post("/slack/events-check", response_model=SlackEventsCheck)
async def slack_events_check() -> SlackEventsCheck:
    """Tests, end to end, that Slack is delivering events here: the bot puts a 👀 on your latest feature-request card (or, with no card yet,
    on a short message it posts and deletes), waits for Slack to report that reaction back, then takes it off. Says which way it failed:
    nothing arrived (Slack is not sending — Event Subscriptions off, a different Request URL, Socket Mode on) or it arrived and was refused
    (the signing secret does not match)."""
    slack, store = _slack(), get_store()
    card = store.latest_card()
    temp_ts: str | None = None
    try:
        if card is not None:
            channel, ts, target = card.slack_channel, card.slack_ts, "your latest feature-request card"
        else:
            channel = _slack_channel_id()
            if not channel:
                return SlackEventsCheck(delivered=False, detail="No Slack channel is configured yet, so there is nothing to react to — set the channel first.")
            temp_ts = ts = await run_in_threadpool(slack.post_message, channel=channel, text="Checking that Slack events reach Vexa — this message deletes itself.")
            target = "a short message it posts and deletes"
        started = time.time()
        await run_in_threadpool(slack.reactions_add, channel=channel, ts=ts, name="eyes")
        refused = False
        while time.time() - started < _EVENTS_CHECK_WAIT_S:
            seen = [d for d in _slack_deliveries if d["at"] >= started]
            if any(d["ok"] and d["reaction"] == "eyes" and d["ts"] == ts for d in seen):
                return SlackEventsCheck(delivered=True, target=target, detail=f"Slack delivered the test reaction in {time.time() - started:.1f}s — events are arriving.")
            refused = any(not d["ok"] for d in seen)
            if refused:
                break
            await asyncio.sleep(0.4)
        if refused:
            return SlackEventsCheck(delivered=False, refused=True, target=target, detail=(
                "Slack sent the event but this service refused its signature — SALES_CYCLE_SLACK_SIGNING_SECRET does not match your Slack app's "
                "Signing Secret (Basic Information → App Credentials)."))
        return SlackEventsCheck(delivered=False, target=target, detail=(
            f"Nothing arrived in {int(_EVENTS_CHECK_WAIT_S)}s — Slack is not sending events here. In your Slack app check: Event Subscriptions is on with this "
            "service's address as the Request URL, reaction_added and reaction_removed are subscribed, and Socket Mode is OFF (with Socket Mode on, Slack "
            "ignores the Request URL)."))
    except SlackError as exc:
        code = exc.error_code or "slack_error"
        why = {"missing_scope": "the Slack app lacks the reactions:write permission — add it and reconnect Slack",
               "not_in_channel": "the app is not in that channel — invite it with /invite",
               "message_not_found": "the card's Slack message no longer exists", "channel_not_found": "the channel was not found"}.get(code, code)
        return SlackEventsCheck(delivered=False, detail=f"Could not run the check: {why}.")
    finally:
        try:
            if card is not None or temp_ts is not None:
                await run_in_threadpool(slack.reactions_remove, channel=(card.slack_channel if card else channel), ts=(card.slack_ts if card else temp_ts), name="eyes")
            if temp_ts is not None:
                await run_in_threadpool(slack.delete_message, channel=channel, ts=temp_ts)
        except Exception:  # noqa: BLE001 — cleaning up must not change the verdict
            logger.warning("could not clean up after the Slack events check", exc_info=True)


class SlackApprovers(BaseModel):
    """Who may give the go-ahead on a feature request. ``configured`` false = nobody set: the original rule applies (anyone's ✅)."""
    user_ids: list[str] = []
    include_admins: bool = False
    usergroup_ids: list[str] = []
    configured: bool = False


class SetSlackApproversBody(BaseModel):
    model_config = {"extra": "forbid"}
    user_ids: list[str] = []
    include_admins: bool = False
    usergroup_ids: list[str] = []


def _slack_approvers() -> SlackApprovers:
    p = load_policy(get_store())
    return SlackApprovers(**p.to_dict(), configured=p.configured)


@app.get("/slack/approvers", response_model=SlackApprovers)
async def get_slack_approvers() -> SlackApprovers:
    """What the Settings page's Slack card shows under "Who can approve"."""
    return _slack_approvers()


@app.post("/slack/approvers", response_model=SlackApprovers)
async def set_slack_approvers(body: SetSlackApproversBody) -> SlackApprovers:
    """Sets who may approve a feature request: Slack member ids (U…), workspace admins/owners, and user group ids (S…) — any one
    source makes a leader. Empty everything to go back to the original rule (anyone's ✅ approves)."""
    try:
        policy = ApproverPolicy.from_input(body.user_ids, body.include_admins, body.usergroup_ids)
    except InvalidPolicy as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    save_policy(get_store(), policy)
    return _slack_approvers()


def _resolve_and_bind(*, api_key: str, platform: str, native_meeting_id: str, customer_tag: str) -> TagResponse:
    company = resolve_by_tag(_hubspot(), customer_tag)
    if company is None:
        raise HTTPException(status_code=404, detail=f"no HubSpot company matched customer_tag={customer_tag!r}")
    workspace_id = slug_for_company(company)
    settings = get_settings()
    try:
        bind_meeting_workspace(
            gateway_url=settings.vexa_gateway_url, api_key=api_key,
            platform=platform, native_meeting_id=native_meeting_id, workspace_id=workspace_id,
        )
    except WorkspaceBindError as e:
        # The meeting/bot is real and running regardless — a failed bind is a triage item, not a
        # reason to fail the caller's request (P18: report, don't swallow, but don't block either).
        logger.error("workspace bind failed for %s/%s → %s: %s", platform, native_meeting_id, workspace_id, e)
        raise HTTPException(status_code=502, detail=f"resolved {workspace_id} but binding failed: {e}") from e
    return TagResponse(workspace_id=workspace_id, company_name=company.name)


@app.post("/tag", response_model=TagResponse)
def tag(body: TagRequest, x_api_key: str = Header(..., alias="X-API-Key")) -> TagResponse:
    return _resolve_and_bind(
        api_key=x_api_key, platform=body.platform, native_meeting_id=body.native_meeting_id,
        customer_tag=body.customer_tag,
    )


@app.post("/dispatch")
def dispatch(body: DispatchRequest, x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    settings = get_settings()
    forward = body.model_dump(exclude={"customer_tag"}, exclude_none=True)
    try:
        resp = httpx.post(
            f"{settings.vexa_gateway_url.rstrip('/')}/bots", json=forward,
            headers={"X-API-Key": x_api_key, "Content-Type": "application/json"}, timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"POST /bots unreachable: {type(e).__name__}: {e}") from e
    if resp.status_code >= 400:
        # Forward Vexa's own error verbatim — this wrapper adds a step, it doesn't hide failures.
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    result = resp.json()
    out: dict = {"bot": result}

    if not body.customer_tag:
        out["workspace"] = {"resolved": False, "reason": "no customer_tag given"}
        return out

    platform = result.get("platform") or body.platform
    native_meeting_id = result.get("native_meeting_id") or body.native_meeting_id
    if not platform or not native_meeting_id:
        out["workspace"] = {"resolved": False, "reason": "platform/native_meeting_id unavailable from response"}
        return out

    try:
        tagged = _resolve_and_bind(
            api_key=x_api_key, platform=platform, native_meeting_id=native_meeting_id,
            customer_tag=body.customer_tag,
        )
        out["workspace"] = {"resolved": True, **tagged.model_dump()}
    except HTTPException as e:
        # The bot is already dispatched — a resolution miss is never a reason to fail the whole call.
        out["workspace"] = {"resolved": False, "reason": e.detail}
    return out


def _expected_signoff(settings: Settings) -> str | None:
    if settings.product_repo_signoff_name and settings.product_repo_signoff_email:
        return f"{settings.product_repo_signoff_name} <{settings.product_repo_signoff_email}>"
    return None


def _dispatch_one(store: Store, settings: Settings, approval: PendingApproval) -> bool:
    """Starts the implementation turn for ONE approved request. Shared by the real-time path (fires
    the moment the ✅ reaction arrives) and the cron sweep (a safety net for anything that path
    missed — e.g. this service was down when the reaction came in). `claim_for_dispatch` makes the
    two paths race-safe: only one of them can ever start a turn for a given approval, since a
    duplicate turn would mean two agents mutating the SAME shared product-repo workspace at once —
    each dispatch also requests its OWN isolated worktree (core/agent's isolation.mode="worktree"),
    so two DIFFERENT approvals dispatched close together can't corrupt each other's git state either."""
    if not store.claim_for_dispatch(approval.id):
        return False  # the other path already claimed it — not an error, just a race we lost
    try:
        result = submit_implementation(
            agent_api_url=settings.agent_api_internal_url,
            subject=settings.product_repo_subject,
            title=approval.title, body=approval.body,
            signoff_name=settings.product_repo_signoff_name, signoff_email=settings.product_repo_signoff_email,
            runner=settings.product_repo_runner,
        )
    except DispatchError:
        logger.exception("dispatch failed for approval id=%s title=%r", approval.id, approval.title)
        store.revert_to_approved(approval.id)
        return False
    store.mark_dispatched(approval.id, branch=result["branch"], workload_id=result.get("workload_id"))
    return True


# ── approval by the team's votes and a leader's go-ahead (see approval_policy.py / approvers.py) ──

_bot_user_ids: dict[str, str] = {}      # bot token → the user id its own (seeded) reactions carry

# What Slack has delivered to /slack/events recently, newest last: used by the delivery check (is Slack actually sending events?) and for the
# "last event" the Settings page shows. In memory, per process; the last times are also kept in runtime_settings so a restart does not forget them.
_slack_deliveries: deque[dict] = deque(maxlen=50)


def _note_slack_delivery(*, ok: bool, event: dict | None = None) -> None:
    now = time.time()
    item = (event or {}).get("item") or {}
    _slack_deliveries.append({"at": now, "ok": ok, "type": (event or {}).get("type"), "reaction": (event or {}).get("reaction"), "ts": item.get("ts")})
    try:
        get_store().set_runtime_setting("slack_last_event_at" if ok else "slack_last_rejected_at", repr(now))
    except Exception:  # noqa: BLE001 — bookkeeping must never fail a delivery
        logger.warning("could not record the Slack delivery time", exc_info=True)


def _bot_user_id(slack: SlackClient) -> str:
    token = slack.bot_token
    if token not in _bot_user_ids:
        _bot_user_ids[token] = slack.auth_test()["user_id"]
    return _bot_user_ids[token]


def _mentions(user_ids) -> str:
    return ", ".join(f"<@{u}>" for u in user_ids)


def _reply(slack: SlackClient, channel: str, ts: str, text: str) -> None:
    """A thread reply is courtesy, never a condition: a failure to post it must not undo or hold back the approval."""
    try:
        slack.post_thread_reply(channel=channel, thread_ts=ts, text=text)
    except SlackError:
        logger.warning("could not post a thread reply on %s/%s", channel, ts, exc_info=True)


def _problem_note(what: str, message: str) -> str:
    """A person-readable account of why the pipeline could not push the agent's branch / open its pull request, said once in the card's thread."""
    if is_auth_failure(message):
        return (f":warning: *The agent finished its draft, but couldn't {what}:* the saved connection to GitHub has expired. An admin can fix it in Vexa — "
                "Settings → Integrations → GitHub → Product repo → Change → Use this repo. It retries on its own, so nothing else is needed.")
    reason = message.split("failed:", 1)[-1].strip()[:240]
    return f":warning: *The agent finished its draft, but couldn't {what}.* It keeps retrying on its own.\n_Reason: {reason}_"


_CI_POLL_EVERY_S = 120.0     # GitHub allows ~60 unauthenticated calls an hour: a pull request's CI takes minutes, so ask every couple


def _check_ci(store: Store, approval) -> None:
    """Reads CI's verdict on an opened pull request and says it once in the card's thread: passed, failed (naming the checks), or that no CI
    ran. Fails open — an unreachable GitHub or a rate limit just waits for the next sweep; a repository that is not public is said once."""
    now = time.time()
    if now - (approval.ci_checked_at or approval.done_at or now) < _CI_POLL_EVERY_S:
        return
    ref = parse_pr_url(approval.pr_url or "")
    if ref is None:
        store.finish_ci(approval.id, "untracked")
        return
    store.record_ci_poll(approval.id)
    try:
        sha = approval.pr_head_sha or fetch_head_sha(ref)
        if not approval.pr_head_sha:
            store.record_ci_poll(approval.id, head_sha=sha)
        runs = fetch_check_runs(ref, sha)
    except GitHubError as exc:
        if exc.status == 404 and store.finish_ci(approval.id, "unreadable"):
            _reply(_slack(), approval.slack_channel, approval.slack_ts,
                   f":grey_question: *Couldn't tell whether the automatic checks passed* — the repository is private and Vexa has no GitHub access to read the result. "
                   f"<{ref.checks_url}|Open the draft on GitHub>")
        else:
            logger.warning("could not read CI for %s (%s) — will try again", approval.pr_url, exc)
        return
    age = now - (approval.done_at or now)
    ignore = ignored_checks(get_settings().ci_ignored_checks)
    result = verdict(runs, age_s=age, checks_url=ref.checks_url, ignore=ignore)
    if result is not None and result[0] == "passed":
        # CI passed — say what it did NOT look at. Best-effort: if the file list or workspace cannot be read, the plain verdict stands.
        try:
            uncovered = uncovered_by_ci(fetch_changed_files(ref), fetch_workspace_globs(ref, sha),
                                        [p.strip() for p in get_settings().ci_standalone_packages.split(",") if p.strip()])
        except GitHubError as exc:
            logger.warning("could not work out what CI left uncovered for %s (%s)", approval.pr_url, exc)
            uncovered = []
        result = verdict(runs, age_s=age, checks_url=ref.checks_url, uncovered=uncovered, ignore=ignore)
    if result is not None and store.finish_ci(approval.id, result[0]):
        _reply(_slack(), approval.slack_channel, approval.slack_ts, result[1])


def _report_pipeline_problem(store: Store, approval, what: str, exc: Exception) -> None:
    """The sweep retries a failed push / pull request forever and quietly; the first time it fails for a given reason, say so in the card's thread."""
    message = str(exc)
    if store.record_error(approval.id, message):
        _reply(_slack(), approval.slack_channel, approval.slack_ts, _problem_note(what, message))


def _evaluate_votes(store: Store, settings: Settings, channel: str, ts: str) -> str:
    """Recounts a card's reactions straight from Slack and acts: a leader's ✅ with more 👍 than 👎 approves it and starts the agent;
    a leader's ✅ without the votes is said so once, and the request is approved the moment the votes are there (the sweep re-checks
    it). Returns "approved", "waiting", "none" or "skipped" (nothing to evaluate / Slack unreachable)."""
    pending = store.pending_for_message(slack_channel=channel, slack_ts=ts)
    policy = load_policy(store)
    if pending is None or not policy.configured:
        return "skipped"
    slack = _slack()
    try:
        bot_id = _bot_user_id(slack)
        reactions = slack.reactions_get(channel=channel, ts=ts)
    except SlackError:
        logger.warning("could not read the reactions on %s/%s — not deciding now", channel, ts, exc_info=True)
        return "skipped"
    resolver = LeaderResolver(policy, slack)
    t = tally(reactions, bot_user_id=bot_id, is_leader=resolver.is_leader)
    outcome = decide(t)
    if outcome == "approve":
        approved = store.approve(slack_channel=channel, slack_ts=ts, approved_by=",".join(t.leader_approvers),
                                 votes_up=t.up, votes_down=t.down)
        if approved is None:
            return "skipped"                           # another evaluation got there first
        logger.info("feature-request approved by %s with %d up / %d down: workspace=%s title=%r",
                    ",".join(t.leader_approvers), t.up, t.down, approved.workspace_id, approved.title)
        _reply(slack, channel, ts, f"Approved by {_mentions(t.leader_approvers)} — {t.up} :+1: / {t.down} :-1:. An agent is implementing it now.")
        _dispatch_one(store, settings, approved)
        return "approved"
    if outcome == "waiting" and store.claim_waiting_notice(pending.id):
        _reply(slack, channel, ts, f"{_mentions(t.leader_approvers)} approved, but it needs more :+1: than :-1: first "
                                   f"(now {t.up} :+1: / {t.down} :-1:). It starts on its own as soon as that is true.")
    return "waiting" if outcome == "waiting" else "none"


@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()
    settings = get_settings()
    try:
        verify_slack_signature(
            signing_secret=settings.slack_signing_secret,
            timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
            signature=request.headers.get("X-Slack-Signature", ""),
            body=body,
        )
    except SlackSignatureError as e:
        # Only a request that says it is from Slack counts as a refused Slack delivery; anyone can POST here, and a stranger's guess must not
        # make the Settings page report a bad signing secret.
        if "slackbot" in request.headers.get("User-Agent", "").lower():
            _note_slack_delivery(ok=False)
        raise HTTPException(status_code=401, detail=str(e)) from e

    payload = await request.json()

    # The one-time URL-verification handshake Slack does when you first register the endpoint.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") == "event_callback":
        event = payload.get("event") or {}
        _note_slack_delivery(ok=True, event=event)
        item = event.get("item") or {}
        store = get_store()
        if event.get("type") in ("reaction_added", "reaction_removed") and is_vote_reaction(event.get("reaction", "")) \
                and load_policy(store).configured:
            # Votes and a leader's ✅: recount from Slack after acknowledging (Slack needs the ack within 3 s).
            background_tasks.add_task(_evaluate_votes, store, settings, item.get("channel", ""), item.get("ts", ""))
        elif event.get("type") == "reaction_added" and event.get("reaction") in ("white_check_mark", "heavy_check_mark"):
            # No approvers configured: the original rule — anyone's ✅ approves.
            approved = store.approve(
                slack_channel=item.get("channel", ""), slack_ts=item.get("ts", ""),
            )
            if approved is None:
                logger.info("reaction on %s/%s — not a pending feature-request message, or already approved",
                            item.get("channel"), item.get("ts"))
            else:
                logger.info("feature-request approved: workspace=%s title=%r", approved.workspace_id, approved.title)
                # Slack needs this ack within 3s — start the implementation turn AFTER responding,
                # not before (process-approved's cron sweep still catches it if this never runs).
                background_tasks.add_task(_dispatch_one, store, settings, approved)
    return {"ok": True}


class PreviewReport(BaseModel):
    state: Literal["ready", "skipped", "failed"]
    url: str | None = None


_PREVIEW_NOTES = {
    "skipped": ":information_source: *No live preview for this draft* — it changes things behind the scenes, and previews can only show changes "
               "to what you see in the terminal for now.",
    "failed": ":warning: *Couldn't build a live preview of this draft.* A developer can look into why.",
}


@app.get("/internal/previews/wanted")
def previews_wanted() -> list[dict]:
    """The agent's opened pull requests that still need a live preview — what the machine that builds previews asks for."""
    out = []
    for a in get_store().list_wanting_preview():
        ref = parse_pr_url(a.pr_url or "")
        if ref is not None:
            out.append({"id": a.id, "pr": ref.number, "title": a.title})
    return out


@app.post("/internal/previews/{approval_id}")
def preview_report(approval_id: int, report: PreviewReport) -> dict:
    """The machine that builds previews says how it went; the first report for a pull request is said once in the card's thread. A link is
    only worth posting when someone on Slack can open it, so a preview served on this machine alone is recorded and not announced."""
    store = get_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="no such request")
    if not store.finish_preview(approval_id, report.state, report.url):
        return {"ok": True, "said": False}
    text = _PREVIEW_NOTES.get(report.state)
    if report.state == "ready" and (report.url or "").startswith("https://"):
        text = (f":eyes: *You can try this draft live:* <{report.url}|Open the preview>. It shows the real thing with your own data, "
                "but it can't change anything.")
    if text:
        _reply(_slack(), approval.slack_channel, approval.slack_ts, text)
    return {"ok": True, "said": text is not None}


@app.post("/internal/process-approved")
def process_approved() -> dict:
    """Independent sweeps, each its own store state: approved → dispatched (start the AI turn),
    dispatched → pushed (check in, push once ready) — or, if it's sat too long with no push,
    dispatched → approved for a retry, or → failed for good (see `Store.fail_or_retry_stale_dispatch`
    and `product_repo_dispatch_timeout_sec`) — and pushed → done (open the PR). Each approval now has
    its OWN isolated worktree (core/agent's isolation.mode="worktree"), so — unlike before that
    existed — git state has to be fetched PER approval, not once for the whole sweep."""
    settings = get_settings()
    store = get_store()
    dispatched_now = []
    pushed_now = []
    opened_now = []
    timed_out_now = []

    # A leader already said go on these; recount the votes so one that tipped without an event reaching us still goes through.
    approved_by_votes = [
        a.id for a in store.list_waiting_for_votes()
        if _evaluate_votes(store, settings, a.slack_channel, a.slack_ts) == "approved"
    ]

    for approval in store.list_approved_unprocessed():
        if _dispatch_one(store, settings, approval):
            dispatched_now.append(approval.id)

    for approval in store.list_dispatched_unpushed():
        # A crashed/hung turn stays 'dispatched' forever otherwise — never checked again after this
        # sweep, since push_if_ready would just keep returning None for it every time. Handled BEFORE
        # the push check, not after: a genuinely stale row has nothing to check push-readiness for.
        if (
            approval.dispatched_at is not None
            and (time.time() - approval.dispatched_at) > settings.product_repo_dispatch_timeout_sec
        ):
            outcome = store.fail_or_retry_stale_dispatch(
                approval.id, max_attempts=settings.product_repo_max_dispatch_attempts,
            )
            if outcome != "skipped":
                logger.warning(
                    "dispatch timed out for approval id=%s branch=%s (attempt %s/%s) → %s",
                    approval.id, approval.branch, approval.dispatch_attempts,
                    settings.product_repo_max_dispatch_attempts, outcome,
                )
                timed_out_now.append({"id": approval.id, "outcome": outcome})
            continue
        try:
            state = git_state(
                agent_api_url=settings.agent_api_internal_url, subject=settings.product_repo_subject,
                unit_id=approval.workload_id, timeout=15.0,
            )
            pushed = push_if_ready(
                state, agent_api_url=settings.agent_api_internal_url,
                subject=settings.product_repo_subject, expected_branch=approval.branch,
                unit_id=approval.workload_id, expected_signoff=_expected_signoff(settings),
            )
        except (DispatchError, PushError) as exc:
            logger.exception("push check failed for approval id=%s branch=%s", approval.id, approval.branch)
            if isinstance(exc, PushError):
                _report_pipeline_problem(store, approval, "save it to GitHub", exc)
            continue
        if pushed is not None:
            store.mark_pushed(approval.id)
            store.clear_error(approval.id)
            pushed_now.append(approval.id)

    for approval in store.list_pushed_unopened():
        try:
            pr = open_pull_request(
                agent_api_url=settings.agent_api_internal_url, subject=settings.product_repo_subject,
                title=approval.title, body=approval.body, base=settings.product_repo_default_branch,
                unit_id=approval.workload_id,
            )
        except PullRequestError as exc:
            logger.exception("pull-request open failed for approval id=%s branch=%s — retrying next sweep",
                              approval.id, approval.branch)
            _report_pipeline_problem(store, approval, "open it for review on GitHub", exc)
            continue
        pr_url = (pr or {}).get("url")
        store.mark_done(approval.id, pr_url=pr_url)
        store.clear_error(approval.id)
        opened_now.append(approval.id)
        if pr_url:
            _reply(_slack(), approval.slack_channel, approval.slack_ts,
                   f":hammer_and_wrench: *The agent has finished a first draft of \"{approval.title}\".* It still needs a developer to review it before anything "
                   f"is used. <{pr_url}|Open the draft on GitHub>")

    for approval in store.list_awaiting_ci():
        _check_ci(store, approval)

    return {
        "dispatched": dispatched_now, "pushed": pushed_now, "opened": opened_now,
        "timed_out": timed_out_now, "approved_by_votes": approved_by_votes,
    }


@app.post("/webhooks/meeting-started")
async def webhook_meeting_started(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()
    settings = get_settings()
    try:
        verify_webhook_signature(
            secret=settings.calendar_webhook_secret,
            timestamp=request.headers.get("X-Webhook-Timestamp", ""),
            signature=request.headers.get("X-Webhook-Signature", ""),
            body=body,
        )
    except WebhookSignatureError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    payload = await request.json()
    if payload.get("event_type") != "meeting.started":
        return {"resolved": False, "reason": "not a meeting.started event"}

    meeting = ((payload.get("data") or {}).get("meeting")) or {}

    meeting_id = meeting.get("id")
    subject = meeting.get("user_id")
    platform = meeting.get("platform")
    native_meeting_id = meeting.get("native_meeting_id")

    # Inviting our bot into a call IS the consent signal — no separate rep action should be needed
    # for the copilot to start watching it. Best-effort: a failure here must never break the webhook
    # response (Vexa retries meeting.started on a non-2xx) or block calendar-path resolution below.
    if settings.auto_process_calls and meeting_id is not None and platform and native_meeting_id:
        background_tasks.add_task(
            enable_copilot_processing,
            agent_api_url=settings.agent_api_internal_url, platform=str(platform),
            native_meeting_id=str(native_meeting_id), meeting_id=str(meeting_id),
        )

    if meeting_id is not None and subject is not None:
        # asyncio.create_task, NOT background_tasks.add_task: BackgroundTasks runs its entries
        # sequentially (Starlette's own documented behavior), and watch_meeting is a persistent
        # watcher that doesn't return until the meeting ends — sharing that queue with anything
        # else would starve whatever was registered after it, forever, for as long as the call
        # lasts. Reproduced live: real copilot processing never turned on because watch_meeting had
        # (at the time) been registered first. A real fix decouples the hazard, not just this one
        # ordering of it — watch_meeting now runs fully independently of BackgroundTasks.
        #
        # _start_watcher no-ops if this meeting already has a live tracked watcher (its own guard,
        # not this caller's) — Vexa can (and did, live) deliver meeting.started more than once for
        # the same meeting.
        _start_watcher(
            agent_api_url=settings.agent_api_internal_url,
            meeting_api_url=settings.meeting_api_internal_url,
            subject=str(subject), meeting_id=str(meeting_id),
        )
    else:
        logger.warning("meeting.started payload missing meeting.id/user_id — no live watcher started")

    own_domains = {d.strip().lower() for d in settings.own_domains.split(",") if d.strip()}
    # resolve_meeting_started (and the fallback bind below) are fully synchronous, blocking HTTP calls
    # (calendar_resolver.py's own `call()` helper, 5s timeout each, tried per candidate domain) — called
    # directly on an `async def` handler, they block the WHOLE event loop, stalling every other
    # concurrent request this process is serving for as long as HubSpot/the gateway take to answer.
    # run_in_threadpool keeps this request's own response waiting on the same result (the webhook body
    # still needs it), it just stops that wait from blocking unrelated requests too.
    result = await run_in_threadpool(
        resolve_meeting_started,
        event=meeting, hubspot=_hubspot(), gateway_url=settings.vexa_gateway_url,
        api_key=settings.calendar_api_key, own_domains=own_domains,
    )
    if not result["resolved"]:
        logger.info("calendar-path resolution miss: %s", result.get("reason"))
        # No confirmed customer match — tag it with the configured fallback instead of leaving
        # workspace_id blank forever (the default, empty, keeps today's exact behavior).
        if settings.fallback_workspace_id and platform and native_meeting_id:
            try:
                await run_in_threadpool(
                    bind_meeting_workspace,
                    gateway_url=settings.vexa_gateway_url, api_key=settings.calendar_api_key,
                    platform=str(platform), native_meeting_id=str(native_meeting_id),
                    workspace_id=settings.fallback_workspace_id,
                )
                result = {"resolved": True, "workspace_id": settings.fallback_workspace_id, "fallback": True}
            except WorkspaceBindError:
                logger.exception("fallback workspace bind failed for %s/%s", platform, native_meeting_id)
    return result


def _hubspot_oauth_config() -> OAuthProviderConfig:
    s = get_settings()
    return OAuthProviderConfig(
        client_id=s.hubspot_oauth_client_id, client_secret=s.hubspot_oauth_client_secret,
        redirect_uri=s.hubspot_oauth_redirect_uri, scopes=s.hubspot_oauth_scopes,
    )


def _slack_oauth_config() -> OAuthProviderConfig:
    s = get_settings()
    return OAuthProviderConfig(
        client_id=s.slack_oauth_client_id, client_secret=s.slack_oauth_client_secret,
        redirect_uri=s.slack_oauth_redirect_uri, scopes=s.slack_oauth_scopes,
    )


register_oauth_routes(
    app, provider="hubspot",
    build_authorize_url=hubspot_oauth.build_authorize_url, exchange_code=hubspot_oauth.exchange_code,
    error_cls=hubspot_oauth.HubSpotOAuthError, get_store=get_store, get_config=_hubspot_oauth_config,
)

register_oauth_routes(
    app, provider="slack",
    build_authorize_url=slack_oauth.build_authorize_url, exchange_code=slack_oauth.exchange_code,
    error_cls=slack_oauth.SlackOAuthError, get_store=get_store, get_config=_slack_oauth_config,
)


register_zoom_routes(app, get_store=get_store)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "sales-cycle"}
