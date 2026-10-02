- **Vexa Capture: a Mac menu-bar app that sends Vexa's bot to calls that aren't on a calendar.** It notices when Zoom or Teams
  holds the microphone, finds the call's join link in your open browser tabs, and asks Vexa to send its bot there; with no link
  to find it can instead capture the call's audio on the Mac. Pair it with one click from **Settings → Integrations → Vexa
  Capture** (a one-time code, no token to copy; the app's key is bot-scoped and revocable from the same card), through the
  site's own address — no extra public host. A setup window walks through the acknowledgement, connection, browser access and
  notifications. The card shows a **Download for Mac** link when the deployment sets `CAPTURE_DOWNLOAD_URL`; `release.sh`
  builds the signed, notarized image and the update feed the app checks. The mechanisms are proven (pairing, relays, link
  parsing, update check, and the ingest with real transcription); a real Zoom call end to end is still to be witnessed. See
  [`clients/capture-mac`](https://github.com/Vexa-ai/vexa/tree/main/clients/capture-mac).
