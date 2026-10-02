# bot_spawn — `POST /bots`

The bot-spawn flow, ported from the parent `meetings.request_bot` CORE happy path. Builds the bot's
invocation, mints the MeetingToken, spawns the meeting-bot workload over the runtime kernel, and
eager-creates the `MeetingSession` keyed by the bot's `connectionId`.

## Front door
- `build_router(repo, runtime)` — the mountable `POST /bots` router (the unified
  `meeting_api.app.create_app` mounts it).
- `request_bot(...)` — the spawn flow (the router's core; callable directly in tests).
- `build_invocation(...)` / `build_workload_spec(...)` / `mint_meeting_token(...)` — the
  `invocation.v1` / `runtime.v1` builders + the stateless MeetingToken minter. Both builders
  validate against the sealed schema **at the seam** before anything ships.
- `MeetingRepo` / `RuntimeClient` ports + `QuotaExceeded` / `MaxBotsExceeded` / `SpawnFailed` /
  `DuplicateMeeting`.
- `adapters.build_production_router(...)` — wire with real SQLAlchemy + the httpx runtime client.
- `fakes` — `InMemoryMeetingRepo` / `FakeRuntimeClient` (offline drivers).

## The flow (P2 core + P3 control-plane)
construct the meeting URL → dedup (409 on a CONCURRENT active prior) → **max-bots pre-check (429)**
→ mint a per-run service identity → **optional service-authority admission before any row/runtime
effect (403 deny, 503 unavailable)**
→ **continue_meeting (reuse a TERMINAL prior row)** or insert a fresh `Meeting` row (status
`requested`) → mint the MeetingToken + build the `invocation.v1` invocation → spawn the `runtime.v1`
`WorkloadSpec` (`profile="meeting-bot"`; the invocation rides as the one `BOT_CONFIG` env var) →
eager-create the `MeetingSession` (`session_uid` == `connectionId`) → write the kernel workload id
back as `bot_container_id` → return the `api.v1` `MeetingResponse` (now listing its `sessions`).

### P3c — `continue_meeting` (sequential multi-bot per meeting)
When the prior meeting for `(platform, native_id)` is TERMINAL (`completed`/`failed`), reuse the
SAME meeting row + add a NEW `MeetingSession` instead of the 409. Transcripts + recordings stay keyed
by the (unchanged) meeting row, so a continued run preserves them. A CONCURRENT second bot (prior
still active) is still rejected (409).

> **Contract decision (api.v1 is SEALED — DO NOT edit it).** The `POST /bots` request body
> (`MeetingCreate`) has **no `additionalProperties: false`** — it is an OPEN object — so an extra
> `continue_meeting` field on the wire is NOT rejected by the frozen schema, and the behaviour ships
> now via an internal request param. **FLAG (lane:contract):** the schema does not *declare*
> `continue_meeting`; exposing it as a documented, typed PUBLIC field on `api.v1` needs a `vN+1`
> (a human-reviewed `lane:contract` change). Same for the response: the listed `sessions[]` ride in
> the open `data.sessions` (MeetingResponse `data` is `additionalProperties:true`), not a new typed
> field — a typed `sessions` field is likewise a `vN+1`. `gate:contract-version` stays green (no
> sealed schema touched).

### `meeting_in_progress` — the caller says the call is live
`POST /bots` with `"meeting_in_progress": true` (boolean; anything else is a 422) rides to the bot as the
`meetingInProgress` field of `invocation.v1`. A Zoom bot normally treats the web client's "Error" page as
"the host hasn't started the meeting yet" and polls for up to ten minutes (a scheduled bot's case); with the
field set the meeting is known to be running, so the page means the link is wrong, and the bot ends the join
within seconds as `validation_error` — a permanent reason, which the join-retry never re-spawns. Like
`continue_meeting` it is accepted off the open `MeetingCreate` body, not declared on `api.v1`. Calendar and
other scheduled joins never set it.

### `capture: "external"` — a call someone else captures
`POST /bots` with `"capture": "external"` (accepted off the open request body like `continue_meeting`; documenting it as a
public typed field needs a `vN+1`, flagged the same way) creates the meeting row through the SAME gates as a bot — STT
configured, service-authority admission, dedupe (409), the concurrency cap — but spawns nothing: no MeetingToken, no
workload, no `bot_container_id`. `data.capture_source` is `"external"`, no `meeting_url` is needed, and the router reports
`joining` → `active` in-process through the lifecycle entry a bot's own callback uses, so the FSM, persistence, the
`meeting.started` webhook and the live fan-out all fire as for a bot. The client that is capturing (the capture ingest,
`core/meetings/services/desktop`) then writes segments to the stream, ends it with a Stop command or its own `completed`
callback, and a capture that goes quiet is converged by the stale-nonterminal sweep like any bot whose workload is gone.

### P3e — max-bots (per-user concurrency)
A pre-check BEFORE the runtime call: count the user's ACTIVE bots (status in
`{requested, joining, awaiting_admission, active}`, **excluding** infra `browser_session` —
parent `meetings.py:1091`) and reject the N+1th with `429` (`MaxBotsExceeded`). The cap arrives as
the gateway's `X-User-Limits` header (resolved upstream from `/internal/validate`, identity.v1).
The runtime kernel's own `owner_quota` → `QuotaExceeded` (→ 429) is the defense-in-depth BACKSTOP.
Join-retry re-spawns and `continue_meeting` sessions count against the same cap.

Tests: `../../../tests/test_bot_spawn.py` · `test_continue_meeting.py` · `test_max_bots.py`.
Join-retry (P3d) lives in the `lifecycle` brick: `lifecycle/retry.py` + `test_join_retry.py`.

### Optional external service authority

`VEXA_SERVICE_AUTHORITY_CONFIG` enables the sealed, policy-free
`service-authority.v1` seam. The request contains authoritative user/service identity, service
mode, frozen transcription provider, concurrency, and lifecycle timing—never an email, payment
provider ID, price, balance, transcription URL, or credential. The exact JSON bytes are signed
with `VEXA_SERVICE_AUTHORITY_SECRET`.

The admitted decision is frozen in `meeting.data.service_authority`. A meeting-api-owned sweep
asks again at every admitted-time + N-minute boundary. An enforced stop is persisted before the
runtime teardown and converges after restart; repeated sweeps never apply the same decision twice.
Observe-only sessions are never later reinterpreted as enforced sessions.

No config means explicit stock OSS allow-all. Once configured, unavailable, malformed, stale, or
cross-bound decisions fail closed. `mode=observe` records the authority response but cannot satisfy
a hosted hard-spend-cap claim.
