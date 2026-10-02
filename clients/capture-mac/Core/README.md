# Core

The app's pure logic — no AppKit, no audio devices — so `../test.sh` can prove it: the `capture.v1` frame and its chunker
(`CaptureFrame.swift`), call start/end detection from audio activity (`CallDetector.swift`), and the ingest URL, refusal
messages and reconnect backoff (`IngestURL.swift`).
