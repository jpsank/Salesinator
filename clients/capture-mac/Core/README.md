# Core

The app's pure logic — no AppKit, no audio devices — so `../test.sh` can prove it: the `capture.v1` frame and its chunker
(`CaptureFrame.swift`), call start/end detection from audio activity (`CallDetector.swift`), and the ingest URL, refusal
messages and reconnect backoff (`IngestURL.swift`).
The pairing link (`ConnectLink.swift`), the setup facts (`Setup.swift`), the recent-calls log (`CallLog.swift`) and update-feed parsing and version comparison (`UpdateCheck.swift`) are here too.
Meeting-link recognition (`MeetingLinks.swift`) and the `POST /bots` request and the meaning of its answers (`BotRequest.swift`) are here too.
