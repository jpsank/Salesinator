# capture-mac — Vexa Capture, the Mac menu-bar app

Notices a Zoom or Microsoft Teams call that is not on any calendar — an instant meeting, a personal room, a call a
customer started — and transcribes it, **without a bot in the call**. It listens to the call app's own audio plus the
rep's microphone and streams both to the stack's [capture ingest](../../core/meetings/services/desktop/README.md),
where each call becomes a normal Vexa meeting (live copilot, Slack feature-request cards, builds).

## How it works

| | |
|---|---|
| **Detect** | CoreAudio says which apps are using the microphone and speaker (no permission needed). A call **starts** when Zoom/Teams has held the mic for 3 s and **ends** after 20 s with neither mic nor speaker, so muting or a quiet stretch never splits a call. A speaker alone — a recording, a join chime — never starts one. |
| **Capture** | The other participants: ScreenCaptureKit taps *only the call app's* output (needs the *Screen & System Audio Recording* permission; no screen is kept). The rep: the default microphone (needs *Microphone*). Both are 16 kHz mono. |
| **Send** | `capture.v1` frames (100 ms) over a WebSocket to `wss://<your capture host>/ingest` with the user's bot-scoped API key. Zoom/Teams use the ingest's mixed lane: the remote mix on channel 999, the mic on 1000 (labelled "You"). A dropped connection reconnects to the same call within the ingest's 20 s grace. |
| **Say so** | The first run asks you to acknowledge that you will tell people on your calls; every capture posts a notification; the menu bar shows `●` while capturing and `Ⅱ` while paused; *Pause* and *Stop* are one click. |

## Build and run

Needs only the Command Line Tools (macOS 13+, Apple silicon) — no Xcode, no SwiftPM:

```bash
./test.sh      # the pure logic: wire frame (byte-identical to the TS codec), chunking, call detection, ingest URL
./build.sh     # → .build/Vexa Capture.app, ad-hoc signed
open ".build/Vexa Capture.app"
```

Set the server address and a bot-scoped API key (Vexa → Settings → Tokens) in the app's Settings; the key is kept in the
Keychain. Add the app to *System Settings → General → Login Items* to start it at login. Ad-hoc signing means macOS asks
for Microphone and Screen Recording again after each rebuild; a distributed build needs a Developer ID signature and
notarization.

## Checks without a call

```bash
APP=".build/Vexa Capture.app/Contents/MacOS/VexaCapture"
$APP --probe                        # what Zoom/Teams are doing with audio, which permissions are granted
$APP --mic-test                     # 2 s of microphone → level
$APP --app-audio-test zoom          # 3 s of Zoom's audio → level (play something in Zoom first)
$APP --play call.wav --mic mic.wav --url wss://<host>/ingest --key <token>   # a WAV through the real send path
VEXA_CAPTURE_SMOKE=1 $APP           # build the menu and exit (no dialogs)
```

## Limits

- Remote participants arrive as one mixed stream: transcripts label the rep "You" and everyone else unnamed (a native app
  has no on-screen speaker cues).
- If the rep uses speakers instead of headphones the mic hears the call too, and the rep's channel repeats what the others
  said. Use headphones.
- A browser-based call (Meet, Zoom web) is the browser extension's job; Teams and Zoom are detected by their desktop apps.
- Windows is not built.
