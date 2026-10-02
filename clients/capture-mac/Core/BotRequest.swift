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

    public enum Removal: Equatable {
        case removed                       // taken off — or already gone, which is what was wanted
        case keyRejected
        case failed(String)
    }

    /// A 404 means no such bot is running (it left with the meeting, or was removed): the goal is met, so it is not a failure.
    public static func interpretStop(status: Int) -> Removal {
        switch status {
        case 200...299, 404: return .removed
        case 401, 403: return .keyRejected
        default: return .failed("Vexa answered \(status)")
        }
    }

    /// The caller's running bots — what the app asks, while its bot is on a call, to learn quickly that the bot is gone.
    public static func running(gateway: String, key: String) -> URLRequest? {
        guard let base = normalized(gateway), let url = URL(string: base + "/bots/status") else { return nil }
        var r = URLRequest(url: url)
        r.setValue(key, forHTTPHeaderField: "X-API-Key")
        r.timeoutInterval = 10
        return r
    }

    public enum Presence: Equatable {
        case running(Set<String>)          // "platform/native id" of every bot still running
        case unknown                       // no usable answer — never a reason to think the bot is gone
    }

    public static func interpretRunning(status: Int, body: Data) -> Presence {
        guard status == 200, let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any],
              let list = (json["running_bots"] ?? json["running"]) as? [[String: Any]] else { return .unknown }
        return .running(Set(list.compactMap { b in
            guard let p = b["platform"] as? String, let n = (b["native_meeting_id"] as? String) ?? (b["platform_specific_id"] as? String) else { return nil }
            return "\(p)/\(n)"
        }))
    }

    /// True only when Vexa answered and this bot is not among the running ones.
    public static func isGone(_ presence: Presence, platform: String, nativeId: String) -> Bool {
        if case .running(let all) = presence { return !all.contains("\(platform)/\(nativeId)") }
        return false
    }

    /// "Who is this key?" — the setup check's proof that Vexa accepts it.
    public static func me(gateway: String, key: String) -> URLRequest? {
        guard let base = normalized(gateway), let url = URL(string: base + "/auth/me") else { return nil }
        var r = URLRequest(url: url)
        r.setValue(key, forHTTPHeaderField: "X-API-Key")
        r.timeoutInterval = 15
        return r
    }

    public enum Me: Equatable {
        case account(String)
        case keyRejected
        case unavailable(String)
    }

    public static func interpretMe(status: Int, body: Data) -> Me {
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        switch status {
        case 200:
            let who = (json?["email"] as? String) ?? (json?["user_id"].map { "user \($0)" }) ?? "this account"
            return .account(who)
        case 401, 403: return .keyRejected
        default: return .unavailable("Vexa answered \(status)")
        }
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
