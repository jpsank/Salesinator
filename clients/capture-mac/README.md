# capture-mac — Vexa Capture, the Mac menu-bar app

Notices a Zoom or Microsoft Teams call that is not on any calendar — an instant meeting, a personal room, a call a
customer started — and gets it into Vexa, in one of two ways:

- **Send a bot** (the default). It finds the call's join link — in the browser tab the rep clicked it from, or on the
  clipboard — and requests Vexa's own bot for it through the normal pipeline (`POST /bots`), exactly as "add bot from URL"
  does. The app is only the trigger. If the call has no link to find, it falls back to capturing audio, or asks the rep to paste
  the link.
- **Capture audio on this Mac**, no bot in the call. It listens to the call app's own audio plus the rep's microphone and
  streams both to the stack's [capture ingest](../../core/meetings/services/desktop/README.md). Either way the call becomes a
  normal Vexa meeting (live copilot, Slack feature-request cards, builds).

## How it works

| | |
|---|---|
| **Find the link** | Open tabs of Chrome, Arc, Brave, Edge, Vivaldi and Safari (through AppleScript — macOS asks once per browser whether Vexa Capture may control it; declining only means that browser isn't searched; Firefox can't be read), then the clipboard. Only Zoom join links (`/j/`, `/wc/…/join`), Teams meeting links and Meet codes are kept; a host's `/s/` start link and any `zak` token are never used. Several candidates → it asks which; none → audio fallback or a paste box. |
| **Send the bot** | `POST {Vexa API}/bots` with `{"meeting_url": …}` and the rep's key. 409 means a bot is already there; the answers (bad key, limit, server trouble) are shown in plain words. The bot is removed when the call ends or from the menu. |
| **Detect** | CoreAudio says which apps are using the microphone and speaker (no permission needed). A call **starts** when Zoom/Teams has held the mic for 3 s and **ends** after 20 s with neither mic nor speaker, so muting or a quiet stretch never splits a call. A speaker alone — a recording, a join chime — never starts one. |
| **Capture** (audio mode) | The other participants: ScreenCaptureKit taps *only the call app's* output (needs the *Screen & System Audio Recording* permission; no screen is kept). The rep: the default microphone (needs *Microphone*). Both are 16 kHz mono. |
| **Send** (audio mode) | `capture.v1` frames (100 ms) over a WebSocket to `wss://<your capture host>/ingest` with the user's bot-scoped API key. Zoom/Teams use the ingest's mixed lane: the remote mix on channel 999, the mic on 1000 (labelled "You"). A dropped connection reconnects to the same call within the ingest's 20 s grace. |
| **Say so** | Setup asks you to acknowledge that you will tell people on your calls; a bot is a visible participant, an audio capture posts a notification. The menu-bar icon shows the state at a glance — watching, working on a call, a bot on the call (green), audio being captured (red), paused, or needs attention (orange); *Pause*, *Stop*, *Remove the bot* and *Skip this call* are one click, and *Recent calls* lists the last five with what was done. |

## Install and connect

Needs only the Command Line Tools (macOS 13+, Apple silicon) — no Xcode, no SwiftPM:

```bash
./test.sh      # the pure logic: wire frame (byte-identical to the TS codec), chunking, call detection, links, pairing
VEXA_ADDRESS=https://terminal.example.com ./install.sh   # builds, copies to ~/Applications, registers the vexacapture:// link, opens it
```

`VEXA_ADDRESS` is the address people open Vexa at; the first-run prompt offers it (default `http://localhost:13000`). `./build.sh`
builds without installing.

**Connect with one click.** Either start from Vexa — **Settings → Integrations → Vexa Capture → Connect a Mac** (it also lists your paired
Macs, when each last did anything, and disconnects one) — or from the app (the setup window, or *Preferences → Connect to Vexa…*). Vexa, signed in as
you, hands the app a one-time code (`vexacapture://connect?code=…`); the app trades it (once, within two minutes) for its own bot-scoped key
named `vexa-capture (Mac)` plus the addresses to use, and shows *Connected as you@company*. Nothing to copy; the key is kept in the Keychain
and never appears in a URL. The app asks "connect to this server?" only for a site it has not been told to trust — one you typed, one you
approved before, or the one the build was made for — so the click in your own Vexa is the only confirmation after the first time, while a
link from anywhere else still has to ask. *Disconnect* forgets the key; the settings window still takes the details by hand.

**Set up Vexa Capture…** is one window, opened on first run and from the menu, with a row per thing that has to be true — you have
acknowledged telling people on your calls, Vexa accepts the key (the address field and *Connect* are right there), each open browser can be
read (*Allow access* is what makes macOS show its "control Chrome?" prompt, up front rather than mid-call), notifications are on, open at
login — each with its own fix button, refreshing as you finish them (including a pairing completed in the browser). After a pairing it
reappears only if something still needs attention.

**One public address.** Unless told otherwise the app is pointed at the terminal's own relays — `/api/capture/relay` for bot requests
(which take only the app's own key, never the terminal's cookie or deployment key) and a `/capture/ingest` WebSocket for audio — so the
site you already expose is the only address needed. A deployment that publishes the gateway and capture service itself sets
`CAPTURE_API_URL` and `CAPTURE_INGEST_URL` in `.env` instead.

**Open at login** is a checkbox in the setup window and *Preferences* (macOS accepts it for an app in an Applications folder). Ad-hoc signing means macOS asks for the
Automation, Microphone and Screen Recording permissions again after each rebuild; a distributed build needs a Developer ID signature and
notarization.

## Checks without a call

```bash
APP="$HOME/Applications/Vexa Capture.app/Contents/MacOS/VexaCapture"
$APP --probe                        # what Zoom/Teams are doing with audio, which permissions are granted
$APP --check-setup                  # the setup checklist (prompts for browser Automation and notifications)
$APP --check-update                 # what the update feed says (VEXA_FETCH_UPDATE=1 also downloads and opens it)
$APP --whoami                       # does Vexa accept the saved key, and as whom
$APP --find-link zoom               # the join link(s) found in your browsers' tabs / clipboard (prints only meeting links)
$APP --send-bot <link>              # send Vexa's bot to a call link with the saved settings (or --api … --key …)
$APP --mic-test                     # 2 s of microphone → level
$APP --app-audio-test zoom          # 3 s of Zoom's audio → level (play something in Zoom first)
$APP --play call.wav --mic mic.wav --url wss://<host>/ingest --key <token>   # a WAV through the real send path
VEXA_CAPTURE_SMOKE=1 $APP           # build the menu and exit (no dialogs)
VEXA_CAPTURE_SHOT=out.png $APP      # render the setup window to a PNG and exit
```

## Releasing a build people can install

`./install.sh` is for you: it signs ad hoc, so macOS treats every rebuild as a new app — it asks for the Microphone, Screen Recording and
Automation permissions again, and for your login password to read the saved key from the Keychain. A build signed with a Developer ID is
one stable app, so none of that repeats, and it opens on anyone's Mac without a warning. `release.sh` makes it:

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=vexa-notary \
VEXA_ADDRESS=https://terminal.example.com VEXA_UPDATE_FEED=https://github.com/<owner>/<repo>/releases/latest/download/latest.json \
./release.sh
```

It needs an Apple Developer account: a *Developer ID Application* certificate in your Keychain, and notarization credentials stored once
with `xcrun notarytool store-credentials <name>` (the header of `release.sh` has the steps). It signs with the hardened runtime and
`App.entitlements` (microphone and browser automation), notarizes and staples the app, packs it into `dist/VexaCapture-<version>.dmg`,
notarizes that too, and writes `dist/latest.json`. Uploading the two files is left to you; it prints the `gh release create` line.

**Updates.** A build made with `VEXA_UPDATE_FEED` checks that address once a day and when you choose *Preferences → Check for updates…*.
If `latest.json` names a newer version the menu gains *Update to x.y.z…*, which downloads the disk image, checks it against the feed's
checksum and opens it — drag Vexa Capture onto Applications. It does not replace itself: the new app is yours to drop in, and your
connection and settings carry over. Bump `CFBundleShortVersionString` in `Info.plist` for each release.

## Limits

- Remote participants arrive as one mixed stream: transcripts label the rep "You" and everyone else unnamed (a native app
  has no on-screen speaker cues).
- If the rep uses speakers instead of headphones the mic hears the call too, and the rep's channel repeats what the others
  said. Use headphones.
- A bot needs the call's link, and a call joined by typing a meeting ID has none to find — that is when it falls back to audio
  or a pasted link. A bot can also wait in a waiting room until the host admits it, or be refused.
- A browser-based call (Meet, Zoom web) is the browser extension's job; Teams and Zoom are detected by their desktop apps.
- Windows is not built.
