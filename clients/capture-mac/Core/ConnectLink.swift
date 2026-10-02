import Foundation

/// Pairing the app with a Vexa deployment without typing anything. In the signed-in user's browser Vexa's
/// `/api/capture/connect` shows a page that opens `vexacapture://connect?code=…&base=<that site>`; the app then trades the
/// one-time code for its own bot-scoped key and the addresses to use (`/api/capture/exchange`). The key never rides a URL.
public enum ConnectLink {
    public struct Request: Equatable {
        public var code: String
        public var base: URL
    }

    public struct Pairing: Equatable {
        public var key: String
        public var api: String
        public var ingest: String
        public var account: String
    }

    /// A Vexa site this app will talk to: https, or plain http on this machine (a local deployment).
    public static func acceptableBase(_ raw: String) -> URL? {
        guard var c = URLComponents(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = c.scheme?.lowercased(), let host = c.host?.lowercased(), !host.isEmpty else { return nil }
        let local = host == "localhost" || host == "127.0.0.1" || host == "[::1]"
        guard scheme == "https" || (scheme == "http" && local) else { return nil }
        c.path = ""; c.query = nil; c.fragment = nil; c.user = nil; c.password = nil
        return c.url
    }

    /// The `vexacapture://connect?code=…&base=…` link, or nil if it is anything else.
    public static func parse(_ url: URL) -> Request? {
        guard url.scheme?.lowercased() == "vexacapture", url.host?.lowercased() == "connect",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let code = items.first(where: { $0.name == "code" })?.value, !code.isEmpty,
              let baseRaw = items.first(where: { $0.name == "base" })?.value, let base = acceptableBase(baseRaw) else { return nil }
        return Request(code: code, base: base)
    }

    /// The page the person opens in a browser to start pairing.
    public static func connectPage(base: String) -> URL? {
        guard let b = acceptableBase(base) else { return nil }
        return URL(string: b.absoluteString + "/api/capture/connect")
    }

    /// This Mac's own account of itself — a stable id, so pairing again replaces this Mac's earlier key rather than adding another,
    /// and its name, so Vexa's Settings can tell Macs apart.
    public struct Device: Equatable {
        public let id: String
        public let name: String
        public init(id: String, name: String) { self.id = id; self.name = name }
    }

    public static func exchangeRequest(_ r: Request, device: Device? = nil) -> URLRequest {
        var req = URLRequest(url: URL(string: r.base.absoluteString + "/api/capture/exchange")!)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body: [String: Any] = ["code": r.code]
        if let d = device { body["device"] = ["id": d.id, "name": d.name] }
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        req.timeoutInterval = 20
        return req
    }

    public enum Exchanged: Equatable {
        case paired(Pairing)
        case failed(String)
    }

    /// What the exchange answered. The addresses are checked here: a pairing that names anything but an http(s) API and a ws(s)
    /// ingest is refused rather than applied.
    public static func interpret(status: Int, body: Data) -> Exchanged {
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        guard status == 200 else { return .failed((json?["error"] as? String) ?? "Vexa answered \(status).") }
        guard let key = json?["key"] as? String, !key.isEmpty,
              let api = json?["api"] as? String, BotRequest.normalized(api) != nil,
              let ingest = json?["ingest"] as? String, IngestURL.build(base: ingest, platform: "zoom", nativeId: "x", apiKey: "x") != nil else {
            return .failed("Vexa's answer wasn't a valid pairing.")
        }
        return .paired(Pairing(key: key, api: api, ingest: ingest, account: (json?["account"] as? String) ?? ""))
    }
}
