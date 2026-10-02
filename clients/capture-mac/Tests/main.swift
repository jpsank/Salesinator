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
    check("by default a call ends 10 s after the last audio — not before", {
        let d3 = CallDetector(); var n: TimeInterval = 0
        func step(_ a: AudioActivity) -> [CallEvent] { n += 1; return d3.update(now: n, activity: [.zoom: a]) }
        for _ in 0..<5 { _ = step(mic) }
        let early = (0..<9).flatMap { _ in step(idle) }
        let late = (0..<3).flatMap { _ in step(idle) }
        return early.isEmpty && late == [.ended(.zoom)]
    }())
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

// ── recognising meeting links ──
do {
    let zoom = "https://us06web.zoom.us/j/81234567890?pwd=abcDEF123"
    check("a Zoom join link keeps its passcode", MeetingLinks.classify(zoom) == MeetingLink(url: zoom, platform: .zoom))
    check("Zoom's web-client join links count", MeetingLinks.classify("https://zoom.us/wc/join/81234567890")?.platform == .zoom && MeetingLinks.classify("https://zoom.us/wc/81234567890/join")?.platform == .zoom)
    check("a host's start link (it carries a zak token) is never offered", MeetingLinks.classify("https://acme.zoom.us/s/81234567890?zak=SECRET") == nil && MeetingLinks.classify("https://zoom.us/wc/81234567890/start?zak=SECRET") == nil)
    check("a zak on a join link is dropped, the passcode kept", MeetingLinks.classify("https://zoom.us/j/81234567890?pwd=abc&zak=SECRET")?.url == "https://zoom.us/j/81234567890?pwd=abc")
    check("a Zoom page that is not a join link does not", MeetingLinks.classify("https://zoom.us/signin") == nil && MeetingLinks.classify("https://zoom.us/profile") == nil && MeetingLinks.classify("https://zoom.us/j/123") == nil)
    check("a look-alike host is not Zoom", MeetingLinks.classify("https://zoom.us.evil.com/j/81234567890") == nil && MeetingLinks.classify("https://notzoom.us/j/81234567890") == nil)
    check("http is refused", MeetingLinks.classify("http://zoom.us/j/81234567890") == nil)
    check("a Teams meetup-join link counts", MeetingLinks.classify("https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=%7b%7d")?.platform == .teams)
    check("so do Teams /meet links", MeetingLinks.classify("https://teams.microsoft.com/meet/123456789012?p=abc")?.platform == .teams && MeetingLinks.classify("https://teams.live.com/meet/9876543210")?.platform == .teams)
    check("a Meet code counts, the Meet home page does not", MeetingLinks.classify("https://meet.google.com/abc-defg-hij")?.platform == .meet && MeetingLinks.classify("https://meet.google.com/") == nil && MeetingLinks.classify("https://meet.google.com/landing") == nil)
    check("a fragment is dropped", MeetingLinks.classify("https://meet.google.com/abc-defg-hij#x")?.url == "https://meet.google.com/abc-defg-hij")
    check("text that is not a URL is ignored", MeetingLinks.classify("hello") == nil && MeetingLinks.classify("") == nil)

    let tabs = ["https://news.example.com/", "https://zoom.us/j/81234567890?pwd=a", "https://teams.microsoft.com/l/meetup-join/x/0", "https://zoom.us/j/81234567890?pwd=a", "https://zoom.us/j/99999999999?pwd=b"]
    check("a Zoom call is offered Zoom links only, once each", MeetingLinks.candidates(in: tabs, platform: .zoom).map(\.url) == ["https://zoom.us/j/81234567890?pwd=a", "https://zoom.us/j/99999999999?pwd=b"])
    check("a Teams call is offered Teams links", MeetingLinks.candidates(in: tabs, platform: .teams).map(\.platform) == [.teams])
    check("the active tab's link comes first", MeetingLinks.candidates(in: tabs, preferred: ["https://zoom.us/j/99999999999?pwd=b"], platform: .zoom).first?.url == "https://zoom.us/j/99999999999?pwd=b")
    check("no links → no candidates", MeetingLinks.candidates(in: ["https://example.com"], platform: .zoom).isEmpty)

    let t0 = Date(timeIntervalSince1970: 1_000_000)
    var clip = ClipboardAge()
    clip.observe(changeCount: 5, now: t0)
    check("what was on the clipboard before the app saw it is never fresh", !clip.isFresh(now: t0, within: 300) && !clip.isFresh(now: t0 + 10_000, within: 300))
    clip.observe(changeCount: 6, now: t0 + 100)
    check("a copy made while running is fresh", clip.isFresh(now: t0 + 120, within: 300))
    check("…until it is old", !clip.isFresh(now: t0 + 100 + 301, within: 300))
    clip.observe(changeCount: 6, now: t0 + 500)
    check("seeing the same clipboard again does not make it newer", !clip.isFresh(now: t0 + 500, within: 300))
}

// ── sending a bot ──
do {
    let r = BotRequest.create(gateway: "https://api.example.com/", key: "K", meetingURL: "https://zoom.us/j/81234567890?pwd=a")
    check("POST /bots with the key and the link", r?.url?.absoluteString == "https://api.example.com/bots" && r?.httpMethod == "POST" && r?.value(forHTTPHeaderField: "X-API-Key") == "K")
    let sent = (try? JSONSerialization.jsonObject(with: r!.httpBody!)) as? [String: Any]
    check("…as the open meeting_url body the server parses, saying the call is live", sent?["meeting_url"] as? String == "https://zoom.us/j/81234567890?pwd=a" && sent?["meeting_in_progress"] as? Bool == true && sent?.count == 2)
    check("a bad gateway address is refused", BotRequest.create(gateway: "ftp://x", key: "K", meetingURL: "u") == nil && BotRequest.create(gateway: "", key: "K", meetingURL: "u") == nil)
    check("the gateway is normalized", BotRequest.normalized(" http://localhost:18056// ") == "http://localhost:18056")
    let s = BotRequest.stop(gateway: "http://g:1", key: "K", platform: "zoom", nativeId: "81234567890")
    check("stopping is DELETE /bots/{platform}/{id}", s?.url?.absoluteString == "http://g:1/bots/zoom/81234567890" && s?.httpMethod == "DELETE")
    func body(_ j: String) -> Data { Data(j.utf8) }
    check("201 → sent, with the platform and id the server chose", BotRequest.interpret(status: 201, body: body(#"{"platform":"zoom","native_meeting_id":"81234567890"}"#)) == .sent(platform: "zoom", nativeId: "81234567890"))
    check("401 → key rejected; 409 → already there; 429 → limit", BotRequest.interpret(status: 401, body: body("{}")) == .keyRejected && BotRequest.interpret(status: 409, body: body("{}")) == .alreadyThere && BotRequest.interpret(status: 429, body: body("{}")) == .limitReached)
    check("Vexa's 403 for the concurrency limit is a limit, any other 403 a rejected key",
          BotRequest.interpret(status: 403, body: body(#"{"detail":{"reason":"concurrency_limit_reached"}}"#)) == .limitReached && BotRequest.interpret(status: 403, body: body(#"{"detail":"Insufficient scope"}"#)) == .keyRejected)
    check("503 carries the server's reason", BotRequest.interpret(status: 503, body: body(#"{"detail":"no transcription backend configured"}"#)) == .unavailable("no transcription backend configured"))
    check("422 is a refusal naming why", BotRequest.interpret(status: 422, body: body(#"{"detail":"unrecognized meeting link"}"#)) == .refused("unrecognized meeting link"))
    check("every outcome has words for the person", [BotRequest.Outcome.alreadyThere, .keyRejected, .limitReached, .unavailable(""), .refused("x"), .sent(platform: "zoom", nativeId: "1")].allSatisfy { !BotRequest.explain($0, platform: "Zoom").isEmpty })
}

// ── pairing with a Vexa deployment ──
do {
    let good = URL(string: "vexacapture://connect?code=abc123&base=https%3A%2F%2Fterminal.example.com")!
    check("a connect link carries the one-time code and the site", ConnectLink.parse(good) == ConnectLink.Request(code: "abc123", base: URL(string: "https://terminal.example.com")!))
    check("a local http site is accepted", ConnectLink.parse(URL(string: "vexacapture://connect?code=a&base=http%3A%2F%2Flocalhost%3A13000")!)?.base.absoluteString == "http://localhost:13000")
    check("plain http to a remote host is refused", ConnectLink.parse(URL(string: "vexacapture://connect?code=a&base=http%3A%2F%2Fevil.example.com")!) == nil)
    check("a link with no code, no site, or another scheme/host is refused",
          ConnectLink.parse(URL(string: "vexacapture://connect?base=https%3A%2F%2Fx.com")!) == nil && ConnectLink.parse(URL(string: "vexacapture://connect?code=a")!) == nil
          && ConnectLink.parse(URL(string: "https://x.com/connect?code=a&base=https%3A%2F%2Fx.com")!) == nil && ConnectLink.parse(URL(string: "vexacapture://other?code=a&base=https%3A%2F%2Fx.com")!) == nil)
    check("a path, query and credentials on the site are dropped", ConnectLink.acceptableBase("https://user:pw@terminal.example.com/some/path?x=1#f")?.absoluteString == "https://terminal.example.com")
    check("the page to open is the site's /api/capture/connect", ConnectLink.connectPage(base: " https://terminal.example.com/ ")?.absoluteString == "https://terminal.example.com/api/capture/connect" && ConnectLink.connectPage(base: "ftp://x") == nil)
    let withDevice = ConnectLink.exchangeRequest(ConnectLink.parse(good)!, device: .init(id: "1a2b3c4d-0000", name: "Julian's MacBook Pro"))
    let sent = (try? JSONSerialization.jsonObject(with: withDevice.httpBody!)) as? [String: Any]
    check("the exchange also tells the site which Mac this is", sent?["code"] as? String == "abc123" && (sent?["device"] as? [String: String]) == ["id": "1a2b3c4d-0000", "name": "Julian's MacBook Pro"])
    let req = ConnectLink.exchangeRequest(ConnectLink.parse(good)!)
    check("the exchange POSTs the code to the site", req.url?.absoluteString == "https://terminal.example.com/api/capture/exchange" && req.httpMethod == "POST" && (try? JSONSerialization.jsonObject(with: req.httpBody!)) as? [String: String] == ["code": "abc123"])
    func body(_ j: String) -> Data { Data(j.utf8) }
    check("a good answer pairs the key, addresses and account",
          ConnectLink.interpret(status: 200, body: body(#"{"key":"K","api":"https://api.example.com","ingest":"wss://cap.example.com/ingest","account":"a@b.c"}"#)) == .paired(.init(key: "K", api: "https://api.example.com", ingest: "wss://cap.example.com/ingest", account: "a@b.c")))
    check("the server's own words are shown when it refuses", ConnectLink.interpret(status: 404, body: body(#"{"error":"That connection link has expired."}"#)) == .failed("That connection link has expired."))
    check("a pairing naming a bad API or ingest address is refused",
          ConnectLink.interpret(status: 200, body: body(#"{"key":"K","api":"ftp://x","ingest":"wss://c/ingest"}"#)) == .failed("Vexa's answer wasn't a valid pairing.")
          && ConnectLink.interpret(status: 200, body: body(#"{"key":"K","api":"https://a","ingest":"https://c"}"#)) == .failed("Vexa's answer wasn't a valid pairing.")
          && ConnectLink.interpret(status: 200, body: body(#"{"api":"https://a","ingest":"wss://c"}"#)) == .failed("Vexa's answer wasn't a valid pairing."))
}

// ── the setup checklist and trusted sites ──
do {
    let items = [SetupItem("Connected", .ok, "as a@b.c"), SetupItem("Chrome", .attention, "allow it under Automation"), SetupItem("Microphone", .info, "only for the audio fallback")]
    check("the checklist marks each line", SetupReport.format(items) == "✓ Connected — as a@b.c\n✗ Chrome — allow it under Automation\n• Microphone — only for the audio fallback")
    check("audio access is fine only with both permissions", AudioAccess.describe(microphone: true, screen: true).ok && !AudioAccess.describe(microphone: true, screen: false).ok && !AudioAccess.describe(microphone: false, screen: true).ok)
    check("it names exactly what is missing", AudioAccess.describe(microphone: false, screen: true).detail.hasPrefix("Needs Microphone to") && AudioAccess.describe(microphone: true, screen: false).detail.hasPrefix("Needs Screen & System Audio Recording to") && AudioAccess.describe(microphone: false, screen: false).detail.hasPrefix("Needs Microphone and Screen & System Audio Recording to"))
    check("a screen-recording miss says what to do when the switch is already on", AudioAccess.describe(microphone: true, screen: false).detail.contains("switch it off and on again") && !AudioAccess.describe(microphone: false, screen: true).detail.contains("switch it off"))
    check("it says when something needs attention", SetupReport.needsAttention(items) && !SetupReport.needsAttention([items[0], items[2]]))
    let site = URL(string: "https://terminal.example.com")!
    check("a site the person already trusts is recognised", TrustedBases.contains(["http://localhost:13000", "https://terminal.example.com/"], site))
    check("another site is not", !TrustedBases.contains(["http://localhost:13000"], site) && !TrustedBases.contains([], site))
    check("a look-alike is not", !TrustedBases.contains(["https://terminal.example.com.evil.net"], site))
    check("trusting a site once is enough", TrustedBases.adding(TrustedBases.adding([], site), site) == ["https://terminal.example.com"])
    let me = BotRequest.me(gateway: "https://t.example.com/api/capture/relay/", key: "K")
    check("the key check is GET /auth/me under the key", me?.url?.absoluteString == "https://t.example.com/api/capture/relay/auth/me" && me?.value(forHTTPHeaderField: "X-API-Key") == "K" && me?.httpMethod == "GET")
    func body(_ j: String) -> Data { Data(j.utf8) }
    check("200 names the account; 401/403 is a rejected key; anything else is trouble",
          BotRequest.interpretMe(status: 200, body: body(#"{"email":"a@b.c"}"#)) == .account("a@b.c") && BotRequest.interpretMe(status: 200, body: body(#"{"user_id":7}"#)) == .account("user 7")
          && BotRequest.interpretMe(status: 401, body: body("{}")) == .keyRejected && BotRequest.interpretMe(status: 403, body: body("{}")) == .keyRejected
          && BotRequest.interpretMe(status: 502, body: body("{}")) == .unavailable("Vexa answered 502"))
}

// ── recent calls ──
do {
    var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "UTC")!
    let now = Date(timeIntervalSince1970: 1_790_000_000)                      // 2026-09-21 14:13:20 UTC
    func rec(_ secondsAgo: Double, _ o: CallRecord.Outcome, _ note: String? = nil) -> CallRecord { CallRecord(platform: "Zoom", when: now.addingTimeInterval(-secondsAgo), outcome: o, note: note) }
    check("a call from today reads 'today HH:mm'", CallLog.line(rec(600, .botSent), now: now, calendar: cal) == "Zoom · today 14:03 · Vexa's bot was sent")
    check("yesterday's says so", CallLog.line(rec(86_400, .audioCaptured), now: now, calendar: cal).hasPrefix("Zoom · yesterday ") && CallLog.line(rec(86_400, .audioCaptured), now: now, calendar: cal).hasSuffix("audio captured on this Mac"))
    check("an older one carries its date", CallLog.line(rec(86_400 * 5, .skipped), now: now, calendar: cal).hasPrefix("Zoom · Sep 16 "))
    check("each outcome has words", [CallRecord.Outcome.botSent, .botAlreadyThere, .audioCaptured, .skipped, .noLink, .failed].allSatisfy { !CallLog.line(CallRecord(platform: "Teams", when: now, outcome: $0), now: now, calendar: cal).isEmpty })
    check("a failure says why", CallLog.line(rec(0, .failed, "Vexa answered 503"), now: now, calendar: cal).hasSuffix("couldn't join — Vexa answered 503"))
    var log: [CallRecord] = []
    for i in 0..<8 { log = CallLog.adding(log, rec(Double(80 - i * 10), .botSent)) }   // each added call is newer than the last
    check("only the latest five are kept, newest first", log.count == 5 && log[0].when > log[4].when)
    check("it survives being saved and loaded", (try? JSONDecoder().decode([CallRecord].self, from: JSONEncoder().encode(log))) == log)
}

// ── the bot going away ──
do {
    check("a 404 on removal means the bot is already gone — not a failure", BotRequest.interpretStop(status: 404) == .removed && BotRequest.interpretStop(status: 200) == .removed && BotRequest.interpretStop(status: 204) == .removed)
    check("a rejected key or a server error on removal is told apart", BotRequest.interpretStop(status: 401) == .keyRejected && BotRequest.interpretStop(status: 502) == .failed("Vexa answered 502"))
    let r = BotRequest.running(gateway: "https://t.example.com/api/capture/relay/", key: "K")
    check("the running-bots request is a GET of bots/status with the key", r?.url?.absoluteString == "https://t.example.com/api/capture/relay/bots/status" && r?.httpMethod == "GET" && r?.value(forHTTPHeaderField: "X-API-Key") == "K")
    let listed = Data(#"{"running_bots":[{"platform":"zoom","native_meeting_id":"111","status":"active"},{"platform":"google_meet","native_meeting_id":"abc-defg"}]}"#.utf8)
    let presence = BotRequest.interpretRunning(status: 200, body: listed)
    check("it reads the platform and meeting of each running bot", presence == .running(["zoom/111": "active", "google_meet/abc-defg": ""]))
    let waiting = BotRequest.interpretRunning(status: 200, body: Data(#"{"running_bots":[{"platform":"zoom","native_meeting_id":"111","status":"joining"},{"platform":"zoom","native_meeting_id":"222","status":"awaiting_admission"},{"platform":"zoom","native_meeting_id":"333","status":"stopping"}]}"#.utf8))
    check("a bot still outside the call after the patience is stalled", BotRequest.hasStalled(waiting, platform: "zoom", nativeId: "111", waited: 61) && BotRequest.hasStalled(waiting, platform: "zoom", nativeId: "222", waited: 61))
    check("…but not before it", !BotRequest.hasStalled(waiting, platform: "zoom", nativeId: "111", waited: 59))
    check("a bot that is in the call, leaving, without a status, gone or unknown is never stalled",
          !BotRequest.hasStalled(presence, platform: "zoom", nativeId: "111", waited: 600) && !BotRequest.hasStalled(waiting, platform: "zoom", nativeId: "333", waited: 600)
          && !BotRequest.hasStalled(presence, platform: "google_meet", nativeId: "abc-defg", waited: 600) && !BotRequest.hasStalled(waiting, platform: "zoom", nativeId: "999", waited: 600)
          && !BotRequest.hasStalled(.unknown, platform: "zoom", nativeId: "111", waited: 600))
    check("only an active bot counts as in the call", BotRequest.isIn(presence, platform: "zoom", nativeId: "111") && !BotRequest.isIn(waiting, platform: "zoom", nativeId: "111")
          && !BotRequest.isIn(presence, platform: "google_meet", nativeId: "abc-defg") && !BotRequest.isIn(.unknown, platform: "zoom", nativeId: "111") && !BotRequest.isIn(presence, platform: "zoom", nativeId: "999"))
    check("a bot that couldn't get in is told apart from one that left", CallLog.line(CallRecord(platform: "Zoom", when: Date(), outcome: .botCouldntJoin)).hasSuffix("Vexa's bot couldn't get into the call") && CallLog.line(CallRecord(platform: "Zoom", when: Date(), outcome: .botLeft)).hasSuffix("Vexa's bot left the call"))
    let several = BotRequest.interpretRunning(status: 200, body: Data(#"{"running_bots":[{"platform":"zoom","native_meeting_id":"111","status":"active"},{"platform":"teams","native_meeting_id":"T1","status":"joining"},{"platform":"zoom","native_meeting_id":"222","status":"stopping"}]}"#.utf8))
    check("a running bot on the same platform is found", BotRequest.runningBot(several, platform: "zoom")?.nativeId == "111" && BotRequest.runningBot(several, platform: "zoom")?.status == "active" && BotRequest.runningBot(several, platform: "teams")?.nativeId == "T1")
    check("a bot that is leaving, on another platform, or unknown is not", BotRequest.runningBot(waiting, platform: "teams") == nil && BotRequest.runningBot(BotRequest.interpretRunning(status: 200, body: Data(#"{"running_bots":[{"platform":"zoom","native_meeting_id":"9","status":"stopping"}]}"#.utf8)), platform: "zoom") == nil && BotRequest.runningBot(.unknown, platform: "zoom") == nil && BotRequest.runningBot(BotRequest.interpretRunning(status: 200, body: Data(#"{"running_bots":[]}"#.utf8)), platform: "zoom") == nil)
    check("a bot that is listed is not gone", !BotRequest.isGone(presence, platform: "zoom", nativeId: "111"))
    check("a bot that is not listed is gone", BotRequest.isGone(presence, platform: "zoom", nativeId: "222"))
    check("an empty list means every bot is gone", BotRequest.isGone(BotRequest.interpretRunning(status: 200, body: Data(#"{"running_bots":[]}"#.utf8)), platform: "zoom", nativeId: "111"))
    check("no answer, an error or an unreadable body never says the bot is gone",
          [BotRequest.interpretRunning(status: 502, body: listed), BotRequest.interpretRunning(status: 200, body: Data("nope".utf8)), BotRequest.interpretRunning(status: 200, body: Data("{}".utf8))]
            .allSatisfy { $0 == .unknown && !BotRequest.isGone($0, platform: "zoom", nativeId: "111") })
}

// ── updates ──
do {
    check("0.10.0 is newer than 0.9.0", UpdateCheck.isNewer("0.10.0", than: "0.9.0"))
    check("the same version is not newer", !UpdateCheck.isNewer("0.2.0", than: "0.2.0") && !UpdateCheck.isNewer("0.2", than: "0.2.0"))
    check("an older version is not newer", !UpdateCheck.isNewer("0.1.9", than: "0.2.0"))
    check("a version that isn't dotted numbers is never an update", !UpdateCheck.isNewer("beta", than: "0.1.0") && !UpdateCheck.isNewer("1.0.0", than: "x"))
    let sum = String(repeating: "ab", count: 32)
    func feed(_ version: String = "0.2.0", url: String = "https://example.com/v.dmg", sha: String? = nil) -> Data {
        try! JSONSerialization.data(withJSONObject: ["version": version, "url": url, "sha256": sha ?? sum])
    }
    check("a well-formed feed is an offer", UpdateCheck.parse(feed()) == UpdateOffer(version: "0.2.0", url: URL(string: "https://example.com/v.dmg")!, sha256: sum))
    check("a download that isn't https is refused", UpdateCheck.parse(feed(url: "http://example.com/v.dmg")) == nil)
    check("a checksum of the wrong shape is refused", UpdateCheck.parse(feed(sha: "abc")) == nil && UpdateCheck.parse(feed(sha: String(repeating: "zz", count: 32))) == nil)
    check("a newer feed → available", UpdateCheck.interpret(status: 200, body: feed(), current: "0.1.0") == .available(UpdateCheck.parse(feed())!))
    check("the same version → up to date", UpdateCheck.interpret(status: 200, body: feed(), current: "0.2.0") == .upToDate)
    if case .unavailable = UpdateCheck.interpret(status: 404, body: Data(), current: "0.1.0") { check("an error status → unavailable", true) } else { check("an error status → unavailable", false) }
    if case .unavailable = UpdateCheck.interpret(status: 200, body: Data("nope".utf8), current: "0.1.0") { check("garbage → unavailable", true) } else { check("garbage → unavailable", false) }
    check("sha256 of 'abc'", UpdateCheck.sha256Hex(Data("abc".utf8)) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    check("feed addresses: https and local only", UpdateCheck.acceptableFeed("https://github.com/x/latest.json") != nil && UpdateCheck.acceptableFeed("http://localhost:8000/f.json") != nil && UpdateCheck.acceptableFeed("http://example.com/f.json") == nil)
}

print(failed == 0 ? "\n✅ capture-mac Core: wire frame, chunking, call detection, ingest URL, meeting links, bot requests, pairing, setup checks, call log, updates." : "\n❌ \(failed) check(s) failed")
exit(failed == 0 ? 0 : 1)
