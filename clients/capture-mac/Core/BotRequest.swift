import Foundation

/// Sending Vexa's own bot to a call — the same `POST /bots` a rep's "add bot from URL" makes — and what its answer means.
public enum BotRequest {
    public static func create(gateway: String, key: String, meetingURL: String) -> URLRequest? {
        guard let base = normalized(gateway), let url = URL(string: base + "/bots") else { return nil }
        var r = URLRequest(url: url)
        r.httpMethod = "POST"
        r.setValue(key, forHTTPHeaderField: "X-API-Key")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try? JSONSerialization.data(withJSONObject: ["meeting_url": meetingURL])
        r.timeoutInterval = 20
        return r
    }

    public static func stop(gateway: String, key: String, platform: String, nativeId: String) -> URLRequest? {
        guard let base = normalized(gateway),
              let p = platform.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let n = nativeId.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              let url = URL(string: "\(base)/bots/\(p)/\(n)") else { return nil }
        var r = URLRequest(url: url)
        r.httpMethod = "DELETE"
        r.setValue(key, forHTTPHeaderField: "X-API-Key")
        r.timeoutInterval = 20
        return r
    }

    /// http(s) only, no trailing slash.
    public static func normalized(_ gateway: String) -> String? {
        guard let c = URLComponents(string: gateway.trimmingCharacters(in: .whitespacesAndNewlines)),
              let s = c.scheme?.lowercased(), s == "http" || s == "https", c.host?.isEmpty == false else { return nil }
        var base = c; base.query = nil; base.fragment = nil
        return base.string?.replacingOccurrences(of: #"/+$"#, with: "", options: .regularExpression)
    }

    public enum Outcome: Equatable {
        case sent(platform: String, nativeId: String)
        case alreadyThere                  // a bot or capture is already on this call — nothing more to do
        case keyRejected
        case limitReached
        case unavailable(String)           // the server can't take it right now (STT not set, overloaded, …)
        case refused(String)               // the link or request was refused
    }

    public static func interpret(status: Int, body: Data) -> Outcome {
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        let detailText: String = {
            if let d = json?["detail"] as? String { return d }
            if let d = json?["detail"] as? [String: Any] { return (d["reason"] as? String) ?? (d["message"] as? String) ?? "" }
            return ""
        }()
        switch status {
        case 200, 201:
            let platform = (json?["platform"] as? String) ?? ""
            let id = (json?["native_meeting_id"] as? String) ?? (json?["platform_specific_id"] as? String) ?? ""
            return .sent(platform: platform, nativeId: id)
        case 401: return .keyRejected
        case 409: return .alreadyThere
        case 429: return .limitReached
        case 403:
            if let d = json?["detail"] as? [String: Any], d["reason"] as? String == "concurrency_limit_reached" { return .limitReached }
            return .keyRejected
        case 503, 502, 500: return .unavailable(detailText)
        default: return .refused(detailText.isEmpty ? "HTTP \(status)" : detailText)
        }
    }

    public static func explain(_ o: Outcome, platform: String) -> String {
        switch o {
        case .sent: return "A Vexa bot is joining your \(platform) call. If the call has a waiting room, admit it."
        case .alreadyThere: return "A Vexa bot or capture is already on this call."
        case .keyRejected: return "Vexa did not accept your API key — check it in Settings."
        case .limitReached: return "You are at your limit of simultaneous meetings."
        case .unavailable(let d): return d.isEmpty ? "The Vexa server can't take this call right now." : "The Vexa server can't take this call: \(d)"
        case .refused(let d): return "Vexa refused the link: \(d)"
        }
    }
}
