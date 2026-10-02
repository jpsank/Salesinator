import Foundation

public enum IngestURL {
    /// ws(s)://host/ingest?platform=…&native_meeting_id=…&api_key=…  — the key rides the URL (the ingest accepts it
    /// there or as an X-API-Key header); it must be percent-encoded and is never logged.
    public static func build(base: String, platform: String, nativeId: String, apiKey: String) -> URL? {
        guard var c = URLComponents(string: base.trimmingCharacters(in: .whitespaces)),
              let scheme = c.scheme?.lowercased(), scheme == "ws" || scheme == "wss", c.host?.isEmpty == false else { return nil }
        if c.path.isEmpty || c.path == "/" { c.path = "/ingest" }
        // Encoded by hand: URLComponents leaves '&', '=' and '+' alone in a query value, and a key containing one
        // would split into a second parameter.
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: unreserved) ?? "" }
        c.percentEncodedQuery = "platform=\(enc(platform))&native_meeting_id=\(enc(nativeId))&api_key=\(enc(apiKey))"
        return c.url
    }

    /// A fresh id for one call. The ingest only needs it to be unique per call and a bare token (no URL characters).
    public static func newCallId(now: Date = Date(), random: () -> UInt32 = { UInt32.random(in: 0...UInt32.max) }) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd-HHmmss"; f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX")
        return "mac-\(f.string(from: now))-\(String(random(), radix: 16))"
    }

    /// What a refused connection means to the person, by the ingest's close code.
    public static func explain(closeCode: Int, reason: String) -> String {
        switch closeCode {
        case 4401: return "Vexa did not accept your API key — check it in Settings."
        case 4409: return "This call is already being captured (a bot or another device)."
        case 4429: return "You are at your limit of simultaneous meetings."
        case 4503: return reason.isEmpty ? "The Vexa server can't take this call right now." : "The Vexa server can't take this call: \(reason)"
        default: return reason.isEmpty ? "Disconnected (\(closeCode))." : reason
        }
    }
    /// A refusal that retrying cannot fix: stop asking.
    public static func isFinal(closeCode: Int) -> Bool { [4401, 4409, 4429].contains(closeCode) }
}

/// Exponential backoff with a ceiling, for reconnecting a dropped capture.
public struct Backoff {
    private var attempt = 0
    private let base: TimeInterval, cap: TimeInterval
    public init(base: TimeInterval = 1, cap: TimeInterval = 15) { self.base = base; self.cap = cap }
    public mutating func next() -> TimeInterval { defer { attempt += 1 }; return min(cap, base * pow(2, Double(attempt))) }
    public mutating func reset() { attempt = 0 }
}
