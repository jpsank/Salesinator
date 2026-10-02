import AppKit
import AVFoundation
import CoreGraphics
import Foundation

/// Command-line checks, for installing and debugging without waiting for a call:
///   VexaCapture --probe                 what each call app is doing with audio, and which permissions are granted
///   VexaCapture --find-link [zoom|teams]  look for the call's join link in the browsers' tabs and the clipboard (prints only
///                                        meeting links; asks macOS for permission to control each browser the first time)
///   VexaCapture --send-bot <link>      send Vexa's bot to a call link, using the saved settings (or --api/--key)
///   VexaCapture --mic-test             listen to the microphone for 2 s and report its level (lights the mic indicator)
///   VexaCapture --app-audio-test zoom   listen to an app's audio for 3 s and report the level (needs Screen Recording)
///   VexaCapture --play call.wav [--mic mic.wav] --url wss://… --key …
///                                        stream a 16 kHz mono WAV through the real chunker, wire format and
///                                        WebSocket client, as if captured live (checks the whole send path)
enum DevCommands {
    static func run(_ args: [String]) -> Bool {
        if args.contains("--probe") { probe(); return true }
        if let i = args.firstIndex(of: "--find-link") { findLink(i + 1 < args.count ? args[i + 1] : "zoom"); return true }
        if let i = args.firstIndex(of: "--send-bot"), i + 1 < args.count { sendBot(args, link: args[i + 1]); return true }
        if args.contains("--mic-test") { micTest(); return true }
        if let i = args.firstIndex(of: "--app-audio-test") { appAudioTest(i + 1 < args.count ? args[i + 1] : "zoom"); return true }
        if let i = args.firstIndex(of: "--play"), i + 1 < args.count { play(args, wav: args[i + 1]); return true }
        return false
    }

    private static func flag(_ args: [String], _ name: String) -> String? {
        guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    static func probe() {
        print("Call apps (CoreAudio, no permission needed):")
        let a = ProcessAudioProbe.activity()
        for p in CallPlatform.allCases {
            let x = a[p] ?? .idle
            print("  \(p.displayName): mic \(x.input ? "IN USE" : "idle"), speaker \(x.output ? "IN USE" : "idle")")
        }
        let mic: String
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: mic = "granted"; case .notDetermined: mic = "not asked yet"; case .denied: mic = "DENIED"; default: mic = "restricted"
        }
        print("Permissions:\n  Microphone: \(mic)\n  Screen & System Audio Recording: \(CGPreflightScreenCaptureAccess() ? "granted" : "not granted")")
        print("Settings: server \(Settings.serverURL), API key \(Settings.apiKey == nil ? "not set" : "set"), acknowledged \(Settings.consentAccepted)")
    }

    private static func level(_ s: [Float]) -> String {
        guard !s.isEmpty else { return "no samples" }
        let rms = (s.reduce(0) { $0 + $1 * $1 } / Float(s.count)).squareRoot()
        return String(format: "%d samples, level %.4f%@", s.count, rms, rms < 0.0005 ? " (silence)" : "")
    }

    static func findLink(_ name: String) {
        let platform: CallPlatform = name.lowercased().hasPrefix("team") ? .teams : .zoom
        let found = BrowserLinks.search(for: platform)
        if !found.blocked.isEmpty { print("Could not read (allow it under Privacy & Security → Automation): \(found.blocked.joined(separator: ", "))") }
        if found.candidates.isEmpty { print("No \(platform.displayName) link found.") }
        for l in found.candidates { print("  \(l.platform.rawValue): \(l.url)") }
    }

    static func sendBot(_ args: [String], link: String) {
        guard let l = MeetingLinks.classify(link) else { print("That is not a Zoom, Teams or Meet join link."); exit(2) }
        let gateway = flag(args, "--api") ?? Settings.gatewayURL
        guard let key = flag(args, "--key") ?? Settings.apiKey else { print("no API key (pass --key or set one in the app)"); exit(2) }
        let done = DispatchSemaphore(value: 0)
        Task {
            let o = await BotClient.send(to: l, gateway: gateway, key: key)
            print(BotRequest.explain(o, platform: l.platform.rawValue)); print("outcome: \(o)")
            done.signal()
        }
        done.wait()
    }

    private final class Samples {
        private let lock = NSLock(); private var all: [Float] = []
        func add(_ s: [Float]) { lock.lock(); all += s; lock.unlock() }
        var snapshot: [Float] { lock.lock(); defer { lock.unlock() }; return all }
    }

    static func micTest() {
        let mic = MicCapture(); let got = Samples()
        mic.onSamples = { s, _ in got.add(s) }
        do { try mic.start() } catch { print("microphone: \(error.localizedDescription)"); exit(1) }
        Thread.sleep(forTimeInterval: 2); mic.stop()
        print("microphone: \(level(got.snapshot))")
    }

    static func appAudioTest(_ name: String) {
        let platform: CallPlatform = name.lowercased().hasPrefix("team") ? .teams : .zoom
        let cap = AppAudioCapture(); let got = Samples()
        cap.onSamples = { s, _ in got.add(s) }
        let done = DispatchSemaphore(value: 0)
        Task {
            do {
                try await cap.start(bundleIDs: platform.bundleIDs, displayName: platform.displayName)
                try await Task.sleep(nanoseconds: 3_000_000_000)
                await cap.stop()
                print("\(platform.displayName) audio: \(level(got.snapshot))")
            } catch { print("\(platform.displayName) audio: \(error.localizedDescription)") }
            done.signal()
        }
        done.wait()
    }

    private static func wav(_ path: String) throws -> [Float] {
        let b = [UInt8](try Data(contentsOf: URL(fileURLWithPath: path)))
        func u16(_ o: Int) -> Int { Int(b[o]) | Int(b[o + 1]) << 8 }
        func u32(_ o: Int) -> Int { u16(o) | u16(o + 2) << 16 }
        guard b.count > 44, String(bytes: b[0..<4], encoding: .ascii) == "RIFF" else { throw NSError(domain: "wav", code: 1, userInfo: [NSLocalizedDescriptionKey: "not a WAV"]) }
        var o = 12; var fmtOK = false
        while o + 8 <= b.count {
            let id = String(bytes: b[o..<o + 4], encoding: .ascii) ?? "", size = u32(o + 4)
            if id == "fmt " { fmtOK = u16(o + 10) == 1 && u32(o + 12) == 16000 && u16(o + 22) == 16 }
            if id == "data" {
                guard fmtOK else { throw NSError(domain: "wav", code: 2, userInfo: [NSLocalizedDescriptionKey: "need 16-bit mono 16 kHz PCM"]) }
                let n = min(size, b.count - o - 8) / 2
                return (0..<n).map { Float(Int16(bitPattern: UInt16(u16(o + 8 + $0 * 2)))) / 32768 }
            }
            o += 8 + size + (size & 1)
        }
        throw NSError(domain: "wav", code: 3, userInfo: [NSLocalizedDescriptionKey: "no data chunk"])
    }

    static func play(_ args: [String], wav path: String) {
        guard let base = flag(args, "--url"), let key = flag(args, "--key") else { print("usage: --play call.wav --url ws://… --key …"); exit(2) }
        let id = IngestURL.newCallId()
        guard let url = IngestURL.build(base: base, platform: flag(args, "--platform") ?? "zoom", nativeId: id, apiKey: key) else { print("bad --url"); exit(2) }
        let remote: [Float], mic: [Float]?
        do { remote = try wav(path); mic = try flag(args, "--mic").map(wav) } catch { print("cannot read audio: \(error.localizedDescription)"); exit(2) }
        let client = IngestClient(url: url)
        let ready = DispatchSemaphore(value: 0)
        var refused: String?
        client.onEvent = { e in
            switch e {
            case .ready: ready.signal()
            case .refused(let why): refused = why; ready.signal()
            case .reconnecting(let n, let d): print("reconnecting (\(n)) in \(d)s")
            }
        }
        client.start()
        guard ready.wait(timeout: .now() + 15) == .success, refused == nil else { print("not connected: \(refused ?? "timed out")"); exit(1) }
        print("connected — streaming \(String(format: "%.1f", Double(remote.count) / 16000)) s as \(id)")
        let rc = FrameChunker(), mc = FrameChunker()
        var at = 0
        while at < max(remote.count, mic?.count ?? 0) {
            let now = Date().timeIntervalSince1970 * 1000
            if at < remote.count { for f in rc.append(Array(remote[at..<min(at + 1600, remote.count)]), arrivedAtMs: now) { client.send(encodeAudioFrame(channel: CaptureChannel.remote, timestampMs: f.timestampMs, samples: f.samples)) } }
            if let m = mic, at < m.count { for f in mc.append(Array(m[at..<min(at + 1600, m.count)]), arrivedAtMs: now) { client.send(encodeAudioFrame(channel: CaptureChannel.mic, timestampMs: f.timestampMs, samples: f.samples)) } }
            at += 1600
            Thread.sleep(forTimeInterval: 0.1)
        }
        Thread.sleep(forTimeInterval: 6)
        client.stop()
        print("done")
        exit(0)
    }
}
