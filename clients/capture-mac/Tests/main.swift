// Self-test for Core/ — compiled together with it and run by ./test.sh (no XCTest: the Command Line Tools have none).
import Foundation

var failed = 0
func check(_ name: String, _ cond: Bool, _ detail: String = "") {
    print("  \(cond ? "✅" : "❌") \(name)\(cond ? "" : "  — \(detail)")")
    if !cond { failed += 1 }
}

// ── the capture.v1 audio frame ──
do {
    let d = encodeAudioFrame(channel: 999, timestampMs: 1_700_000_000_123.5, samples: [0.5, -0.25])
    check("frame = 12-byte header + 4 bytes a sample", d.count == 12 + 8, "\(d.count)")
    let ch = d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: 0, as: Int32.self) }
    let ts = d.withUnsafeBytes { Double(bitPattern: $0.loadUnaligned(fromByteOffset: 4, as: UInt64.self)) }
    let s0 = d.withUnsafeBytes { Float(bitPattern: $0.loadUnaligned(fromByteOffset: 12, as: UInt32.self)) }
    let s1 = d.withUnsafeBytes { Float(bitPattern: $0.loadUnaligned(fromByteOffset: 16, as: UInt32.self)) }
    check("channel, time and samples round-trip little-endian", ch == 999 && ts == 1_700_000_000_123.5 && s0 == 0.5 && s1 == -0.25, "\(ch) \(ts) \(s0) \(s1)")
    check("channel numbers match the ingest's mixed lane", CaptureChannel.remote == 999 && CaptureChannel.mic == 1000)
    // The same bytes the TypeScript codec (@vexa/capture-codec encodeAudioFrame) writes for the same input — checked
    // by decoding this with its decodeAudioFrame and re-encoding it, so the two ends cannot drift apart silently.
    check("byte-identical to the TypeScript codec's frame", d.map { String(format: "%02x", $0) }.joined() == "e703000000b88756febc78420000003f000080be")
}

// ── chunking into 100 ms frames ──
do {
    let c = FrameChunker()
    var frames = c.append(Array(repeating: 0.1, count: 1000), arrivedAtMs: 10_000)
    check("under 100 ms: nothing yet", frames.isEmpty)
    frames = c.append(Array(repeating: 0.1, count: 2300), arrivedAtMs: 10_206.25)
    check("3300 samples → two 1600-sample frames", frames.count == 2 && frames.allSatisfy { $0.samples.count == 1600 }, "\(frames.count)")
    check("the first frame is stamped when its audio began, not when it filled",
          abs(frames[0].timestampMs - (10_000 - 1000.0 / 16000 * 1000)) < 0.001, "\(frames[0].timestampMs)")
    check("the next frame follows 100 ms later", abs(frames[1].timestampMs - frames[0].timestampMs - 100) < 0.001)
    let rest = c.append(Array(repeating: 0.1, count: 1500), arrivedAtMs: 10_300)
    check("the 100 samples left over carry into the next frame", rest.count == 1, "\(rest.count)")
}

// ── call detection ──
do {
    let d = CallDetector(startAfter: 3, endAfter: 20)
    let mic = AudioActivity(input: true, output: true), spk = AudioActivity(input: false, output: true), idle = AudioActivity.idle
    var t: TimeInterval = 0
    func tick(_ a: AudioActivity, _ platform: CallPlatform = .zoom) -> [CallEvent] { t += 1; return d.update(now: t, activity: [platform: a]) }

    check("playing audio alone never starts a call", (0..<10).allSatisfy { _ in tick(spk).isEmpty } && !d.isInCall(.zoom))
    check("a brief mic use (a settings test) does not start one", { _ = tick(mic); _ = tick(mic); return tick(idle).isEmpty && !d.isInCall(.zoom) }())
    var started: [CallEvent] = []
    for _ in 0..<5 { started += tick(mic) }
    check("holding the mic for 3 s starts the call, once", started == [.started(.zoom)], "\(started)")
    check("…and it is reported as in a call", d.isInCall(.zoom))
    check("muting (no mic, still hearing) keeps the call", (0..<60).allSatisfy { _ in tick(spk).isEmpty } && d.isInCall(.zoom))
    var ended: [CallEvent] = []
    for _ in 0..<25 { ended += tick(idle) }
    check("20 s of no audio at all ends it, once", ended == [.ended(.zoom)], "\(ended)")
    check("a flicker inside the end window does not end it", {
        let d2 = CallDetector(startAfter: 1, endAfter: 20); var n: TimeInterval = 0
        func step(_ a: AudioActivity) -> [CallEvent] { n += 1; return d2.update(now: n, activity: [.zoom: a]) }
        _ = step(mic); _ = step(mic)
        var e: [CallEvent] = []
        for i in 0..<60 { e += step(i % 15 == 14 ? spk : idle) }
        return e.isEmpty && d2.isInCall(.zoom)
    }())
    let both = CallDetector(startAfter: 1, endAfter: 5)
    _ = both.update(now: 0, activity: [.zoom: mic, .teams: idle])
    let first = both.update(now: 2, activity: [.zoom: mic, .teams: idle])
    let second = both.update(now: 4, activity: [.zoom: mic, .teams: mic])
    let third = both.update(now: 6, activity: [.zoom: mic, .teams: mic])
    check("platforms are tracked independently", first == [.started(.zoom)] && second.isEmpty && third == [.started(.teams)] && both.isInCall(.zoom), "\(first) \(second) \(third)")
}

// ── the ingest URL ──
do {
    let u = IngestURL.build(base: "wss://capture.example.com", platform: "zoom", nativeId: "mac-1", apiKey: "k e+y&=")
    check("a bare host gets /ingest", u?.path == "/ingest", u?.absoluteString ?? "nil")
    check("a key with &, = and + stays ONE parameter, fully encoded", u?.absoluteString == "wss://capture.example.com/ingest?platform=zoom&native_meeting_id=mac-1&api_key=k%20e%2By%26%3D", u?.absoluteString ?? "nil")
    check("…and decodes back to the key", URLComponents(url: u!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "api_key" }?.value == "k e+y&=")
    check("platform and id are carried", u?.query?.contains("platform=zoom") == true && u?.query?.contains("native_meeting_id=mac-1") == true)
    check("a non-websocket URL is refused", IngestURL.build(base: "https://x", platform: "zoom", nativeId: "a", apiKey: "k") == nil)
    check("an empty host is refused", IngestURL.build(base: "wss://", platform: "zoom", nativeId: "a", apiKey: "k") == nil)
    let id = IngestURL.newCallId(now: Date(timeIntervalSince1970: 0), random: { 0xabc })
    check("a call id is a bare token the ingest accepts", id == "mac-19700101-000000-abc" && !id.contains(where: { "?#&=/ ".contains($0) }), id)
    check("each refusal says what to do", IngestURL.explain(closeCode: 4401, reason: "").contains("API key") && IngestURL.explain(closeCode: 4429, reason: "").contains("limit"))
    check("auth/duplicate/limit refusals are final; a server fault is retried", IngestURL.isFinal(closeCode: 4401) && IngestURL.isFinal(closeCode: 4409) && !IngestURL.isFinal(closeCode: 4503) && !IngestURL.isFinal(closeCode: 1006))
    var b = Backoff(base: 1, cap: 15)
    check("backoff doubles to a ceiling and resets", [b.next(), b.next(), b.next(), b.next(), b.next(), b.next()] == [1, 2, 4, 8, 15, 15] && { b.reset(); return b.next() == 1 }())
}

print(failed == 0 ? "\n✅ capture-mac Core: wire frame, chunking, call detection, ingest URL." : "\n❌ \(failed) check(s) failed")
exit(failed == 0 ? 0 : 1)
