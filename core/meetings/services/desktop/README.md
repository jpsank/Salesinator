# desktop — the meetings all-in-one capture host (Node/TS)

`@vexa/desktop` (`startDesktop()`) is the meetings data plane composed into **one local process** —
no Docker / Postgres / Redis. It accepts the browser extension's `capture.v1` audio over an ingest
WebSocket, routes it dual-lane (`gmeet-pipeline` per-channel for Google Meet, `mixed-pipeline` for
zoom/teams/youtube), drives **real STT**, and serves transcripts + assembled recordings over an HTTP
gateway. TypeScript because it runs the same browser-adjacent bricks the cloud splits across
meeting-api + collector + gateway, here as a single deployable "modular monolith."

## Two ways to run it

- **Local host** (`startDesktop()` / `pnpm dev`): one process for one user — nothing to authenticate, transcripts kept
  in memory and served from the local gateway. The browser extension's original home.
- **Capture ingest** (`src/stack-main.ts`, compose service `capture`): the same ingest, run as a stack service for
  everyone. Each connection must carry the user's own Vexa API key (`?api_key=` or `X-API-Key`); the call is
  registered as a real meeting through the stack's gateway (`POST /bots` with `capture: "external"` — no bot is
  spawned, and the stack's STT gate, one-meeting-per-call dedupe, concurrency cap and the user's webhooks all apply);
  confirmed segments go to the `transcription_segments` stream the collector drains, so the live copilot, Slack
  cards and builds work exactly as for a bot; a **Stop** in Vexa (`leave` on the meeting's command channel) ends the
  capture; and the end is reported to meeting-api's lifecycle callback. A client that drops and reconnects within 20 s
  stays one meeting. The host keeps no transcript or recording of its own, takes no recording uploads, caps a frame at
  1 MiB and calls at `CAPTURE_MAX_SESSIONS`, and binds its HTTP gateway to loopback. A refused connection is closed
  with `4401` (key not accepted), `4409` (that call already has a bot or capture), `4429` (concurrency limit) or
  `4503` (the stack cannot take it, or the host is full).

  Environment: `TRANSCRIPTION_SERVICE_URL`/`_TOKEN`, `CAPTURE_GATEWAY_URL`, `CAPTURE_MEETING_API_URL`,
  `INTERNAL_API_SECRET`, `REDIS_URL`, `CAPTURE_INGEST_PORT` (9099), `CAPTURE_MAX_SESSIONS` (20). Check a deployment
  end to end with `scripts/stream-audio.ts` (plays a WAV in as a live call).

## Seams

| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| consumes | browser extension | ingest `WS :9099` (`capture.v1` frames, codec-discriminated) | audio frames (ch999 mix / ch1000 mic / per-channel gmeet) + active-speaker event hints |
| consumes | browser extension | `POST /extension/sessions`, `POST /extension/sessions/end`, `POST /telemetry` | session mint / finalize-now (Stop contract) / diagnostics ring buffer |
| consumes | extension | ingest WS (`recording.v1` chunks, same socket, `REC1`-magic discriminated) | recording chunks → `RecordingSink` |
| calls | `@vexa/transcribe-whisper` | `TranscriptionClient` over `TRANSCRIPTION_SERVICE_URL` | PCM → `stt.v1` segments |
| produces | transcript readers / live UI | gateway `GET /transcripts/{p}/{n}`, `WS /ws` | `transcript.v1`-shaped confirmed/pending segments + `health` frames |
| produces | recording readers | gateway `GET /recordings/{p}/{n}` (+ `/player`), `GET /bots`, `GET /health` | assembled `recording.v1` master, meeting list, liveness |

## Contracts

**Owns:** none — the gateway shapes are local HTTP, not a sealed `*.v1` it defines.
**Consumes:** [`core/meetings/contracts/transcript.v1`](../../contracts/transcript.v1) (the transcript
envelope it emits over `/ws` + `/transcripts`); `capture.v1` / `recording.v1` / `stt.v1` are owned by
the `@vexa/capture-codec`, `@vexa/recording`, and `@vexa/transcribe-whisper` packages it composes, not
by the meetings `contracts/` registry. Access is mediated through one `canAccess` seam (`access.ts`,
P20 / ADR-0012; default `ownerOnly` = allow-all on single-user localhost).

## Isolated evaluation

Tests live alongside the source in `src/*.test.ts`. Run with `pnpm test` (the package's `test` script
runs each via `tsx`):

- **L2 unit** — `recording-sink.test.ts`, `transcript-store.test.ts`, `health.test.ts`, `access.test.ts`
- **L3 integration** — `recording-e2e.test.ts` (synthetic `recording.v1` over the real ingest WS → real file on disk → served by the gateway; no live meeting)
- **L4 live** — `desktop-e2e.live.test.ts` (real STT; skips without `VEXA_TX_KEY` + `EVAL_CACHE`)

`pnpm check:isolation` enforces the brick-boundary rule.

## Status

- ✅ delivered — dual-lane ingest (gmeet per-channel + mixed pyannote-cut) with real STT
- ✅ delivered — gateway: sessions mint/end, `/transcripts`, `/bots`, `/health`, telemetry ring buffer, live `/ws`
- ✅ delivered — `recording.v1` receiver → assembled master + dependency-free `/player`
- ✅ delivered — `canAccess` mediation seam on every read path (default owner-only)
- ✅ delivered — capture ingest (stack mode): authenticated clients, calls registered as stack meetings, segments to the collector's stream, Stop and end reported, reconnect grace
- ✅ delivered — P18 fault surfacing (engine fault + no-signal watchdog → `/ws health` · `/telemetry` · log)
- 🟡 partial — store is in-memory single-process (sqlite persistence is a later refinement)
- 🟡 partial — access grants are the seam only (`ownerOnly`); real owner/visibility grants land additively (ADR-0003)
