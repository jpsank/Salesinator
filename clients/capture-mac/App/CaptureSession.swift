import Foundation

/// One call being captured: the ingest connection, the other participants' audio (the call app's output) on the remote
/// channel, the rep's microphone on the mic channel. Pausing drops audio but keeps the connection, so resuming
/// continues the same meeting.
final class CaptureSession {
    enum State: Equatable {
        case connecting
        case capturing
        case paused
        case reconnecting
        case problem(String)        // couldn't start or was refused — says what to do
        case stopped
    }

    let platform: CallPlatform
    let callId: String
    var onState: ((State) -> Void)?

    private let client: IngestClient
    private let appAudio = AppAudioCapture()
    private let mic = MicCapture()
    private let remoteChunker = FrameChunker()
    private let micChunker = FrameChunker()
    private let lock = NSLock()
    private var paused = false
    private var started = false
    private var stopped = false

    init?(platform: CallPlatform, serverURL: String, apiKey: String) {
        let id = IngestURL.newCallId()
        guard let url = IngestURL.build(base: serverURL, platform: platform.rawValue, nativeId: id, apiKey: apiKey) else { return nil }
        self.platform = platform
        self.callId = id
        client = IngestClient(url: url)
        client.onEvent = { [weak self] e in self?.handle(e) }
        appAudio.onSamples = { [weak self] s, at in self?.feed(s, at, channel: CaptureChannel.remote, chunker: self?.remoteChunker) }
        mic.onSamples = { [weak self] s, at in self?.feed(s, at, channel: CaptureChannel.mic, chunker: self?.micChunker) }
        appAudio.onFailure = { [weak self] e in self?.fail("Audio capture stopped: \(e.localizedDescription)") }
    }

    func start() {
        onState?(.connecting)
        client.start()
    }

    func pause() { lock.lock(); paused = true; lock.unlock(); onState?(.paused) }
    func resume() { lock.lock(); paused = false; lock.unlock(); onState?(.capturing) }
    var isPaused: Bool { lock.lock(); defer { lock.unlock() }; return paused }

    func stop() {
        lock.lock(); let already = stopped; stopped = true; lock.unlock()
        guard !already else { return }
        mic.stop()
        Task { await appAudio.stop() }
        client.stop()
        onState?(.stopped)
    }

    private func feed(_ samples: [Float], _ arrivedAtMs: Double, channel: Int32, chunker: FrameChunker?) {
        lock.lock(); let drop = paused || stopped; lock.unlock()
        guard !drop, let chunker else { return }
        for f in chunker.append(samples, arrivedAtMs: arrivedAtMs) {
            client.send(encodeAudioFrame(channel: channel, timestampMs: f.timestampMs, samples: f.samples))
        }
    }

    private func handle(_ e: IngestClient.Event) {
        switch e {
        case .ready:
            lock.lock(); let first = !started; started = true; lock.unlock()
            if first { startCaptures() }
            onState?(isPaused ? .paused : .capturing)
        case .reconnecting: onState?(.reconnecting)
        case .refused(let why): stop(); onState?(.problem(why))
        }
    }

    private func startCaptures() {
        do { try mic.start() } catch { fail("Microphone: \(error.localizedDescription)"); return }
        Task {
            do { try await appAudio.start(bundleIDs: platform.bundleIDs, displayName: platform.displayName) }
            catch { fail("Couldn't capture \(platform.displayName)'s audio: \(error.localizedDescription). Allow Vexa Capture under System Settings → Privacy & Security → Screen & System Audio Recording.") }
        }
    }

    private func fail(_ message: String) {
        stop()
        onState?(.problem(message))
    }
}
