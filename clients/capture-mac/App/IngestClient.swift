import Foundation

/// The WebSocket to the capture ingest: connects, waits for the ingest's "ready", sends audio frames, and — if the
/// connection drops mid-call — comes back on the SAME call id with growing delays (the ingest keeps a dropped call
/// open for 20 s, so a reconnect inside that stays one meeting). A refusal retrying cannot fix (wrong key, the call
/// already captured, over the limit) is reported once and not retried.
final class IngestClient: NSObject, URLSessionWebSocketDelegate {
    enum Event {
        case ready
        case reconnecting(attempt: Int, in: TimeInterval)
        case refused(String)
    }

    var onEvent: ((Event) -> Void)?
    private let url: URL
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let lock = NSLock()
    private var isReady = false
    private var stopped = false
    private var attempts = 0
    private var backoff = Backoff()
    private let queue = DispatchQueue(label: "ai.vexa.capture.ingest")

    init(url: URL) {
        self.url = url
        super.init()
        let q = OperationQueue(); q.maxConcurrentOperationCount = 1; q.underlyingQueue = queue
        session = URLSession(configuration: .default, delegate: self, delegateQueue: q)
    }

    func start() { queue.async { self.connect() } }

    func stop() {
        lock.lock(); stopped = true; isReady = false; lock.unlock()
        queue.async { self.task?.cancel(with: .normalClosure, reason: nil); self.session.invalidateAndCancel() }
    }

    /// Frames sent while not ready are dropped on purpose: a stalled network must not grow memory, and stale audio
    /// delivered late is worse than a gap.
    func send(_ frame: Data) {
        lock.lock(); let ok = isReady && !stopped; lock.unlock()
        guard ok else { return }
        task?.send(.data(frame)) { _ in }
    }

    private func connect() {
        lock.lock(); let s = stopped; lock.unlock()
        guard !s else { return }
        let t = session.webSocketTask(with: url)
        task = t
        t.resume()
        receive(on: t)
    }

    private func receive(on t: URLSessionWebSocketTask) {
        t.receive { [weak self] result in
            guard let self, t === self.task else { return }
            switch result {
            case .failure: return                                  // the delegate's close/complete callback handles it
            case .success(let message):
                if case .string(let text) = message, text.contains("\"ready\"") {
                    self.lock.lock(); self.isReady = true; self.lock.unlock()
                    self.backoff.reset(); self.attempts = 0
                    self.onEvent?(.ready)
                }
                self.receive(on: t)
            }
        }
    }

    private func dropped(closeCode: Int?, reason: String) {
        lock.lock(); let wasStopped = stopped; isReady = false; lock.unlock()
        guard !wasStopped else { return }
        if let code = closeCode, IngestURL.isFinal(closeCode: code) {
            lock.lock(); stopped = true; lock.unlock()
            onEvent?(.refused(IngestURL.explain(closeCode: code, reason: reason)))
            return
        }
        attempts += 1
        let delay = backoff.next()
        onEvent?(.reconnecting(attempt: attempts, in: delay))
        queue.asyncAfter(deadline: .now() + delay) { self.connect() }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        guard webSocketTask === task else { return }
        dropped(closeCode: Int(closeCode.rawValue), reason: reason.flatMap { String(data: $0, encoding: .utf8) } ?? "")
    }

    func urlSession(_ session: URLSession, task t: URLSessionTask, didCompleteWithError error: Error?) {
        guard t === task, error != nil else { return }
        dropped(closeCode: nil, reason: error?.localizedDescription ?? "")
    }
}
