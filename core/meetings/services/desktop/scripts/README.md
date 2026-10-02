# desktop/scripts

[`check-isolation.js`](check-isolation.js) — the service's `gate:isolation` (P2) check: every
`src/` import must be intra-package, a Node builtin, or a declared dep (the composed bricks
`@vexa/*` + `ws` + devDeps) — never another brick's internals.

> The live transcript-dynamics harness moved to `@vexa/eval` — run `pnpm observe` (see
> `meetings/eval/src/observe.mjs`). Set `VEXA_SEG_DEBUG=1` on this desktop process to also
> log pyannote's split boundaries + class context.

[`stream-audio.ts`](stream-audio.ts) — plays a 16 kHz mono WAV into a capture ingest as a live call (remote audio on the mix channel, an optional `--mic` file as "You"), with a real Vexa API key in `VEXA_API_KEY`. The end-to-end check for a deployment's capture ingest.
