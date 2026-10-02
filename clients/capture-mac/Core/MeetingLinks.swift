import Foundation

/// A meeting join link found on this Mac, and which platform it is for.
public struct MeetingLink: Equatable {
    public enum Platform: String { case zoom, teams, meet }
    public var url: String
    public var platform: Platform
    public init(url: String, platform: Platform) { self.url = url; self.platform = platform }
}

/// Recognises meeting join links among arbitrary URLs (browser tabs, the clipboard). It only has to tell a join link
/// from everything else: the Vexa server parses the platform, meeting id and passcode out of the link itself, so what
/// is returned is the link exactly as found — including the passcode in its query.
public enum MeetingLinks {
    public static func classify(_ raw: String) -> MeetingLink? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let c = URLComponents(string: trimmed), let scheme = c.scheme?.lowercased(), scheme == "https",
              let host = c.host?.lowercased() else { return nil }
        let path = c.path

        if host == "zoom.us" || host.hasSuffix(".zoom.us") || host == "zoomgov.com" || host.hasSuffix(".zoomgov.com") {
            // /j/<id>, /wc/join/<id> and /wc/<id>/join (the web client). Never a /s/ or /start link: those are the HOST's
            // way in and carry a `zak` token that must not leave this Mac.
            if matches(path, #"^/j/\d{9,11}/?$"#) || matches(path, #"^/wc/join/\d{9,11}/?$"#) || matches(path, #"^/wc/\d{9,11}/join/?$"#) {
                return MeetingLink(url: strippingFragment(c), platform: .zoom)
            }
            return nil
        }
        if host == "teams.microsoft.com" || host == "teams.cloud.microsoft" || host == "teams.live.com" {
            if path.hasPrefix("/l/meetup-join/") || matches(path, #"^/meet/[^/]+"#) {
                return MeetingLink(url: strippingFragment(c), platform: .teams)
            }
            return nil
        }
        if host == "meet.google.com", matches(path, #"^/[a-z]{3}-[a-z]{4}-[a-z]{3}/?$"#) {
            return MeetingLink(url: strippingFragment(c), platform: .meet)
        }
        return nil
    }

    /// The links worth offering for a detected call, best first, without repeats. A Zoom call gets Zoom links only.
    /// ``preferred`` are URLs known to be a browser's ACTIVE tab (the one being looked at), which go first.
    public static func candidates(in urls: [String], preferred: [String] = [], platform: CallPlatform?) -> [MeetingLink] {
        var seen = Set<String>(), out: [MeetingLink] = []
        for u in preferred + urls {
            guard let l = classify(u), !seen.contains(l.url) else { continue }
            if let p = platform, !fits(l.platform, p) { continue }
            seen.insert(l.url); out.append(l)
        }
        return out
    }

    private static func fits(_ link: MeetingLink.Platform, _ app: CallPlatform) -> Bool {
        switch app { case .zoom: return link == .zoom; case .teams: return link == .teams }
    }

    private static func matches(_ s: String, _ pattern: String) -> Bool { s.range(of: pattern, options: .regularExpression) != nil }
    /// The link as found, minus its fragment and any host-authentication token (`zak`, which would let the bot act as the host).
    private static func strippingFragment(_ c: URLComponents) -> String {
        var x = c; x.fragment = nil
        if let q = x.queryItems { let kept = q.filter { $0.name.lowercased() != "zak" }; x.queryItems = kept.isEmpty ? nil : kept }
        return x.string ?? ""
    }
}

/// When the clipboard last changed, as far as this run has seen. A link on the clipboard is only evidence of the call being
/// joined if it was copied for it, a moment ago — one left over from an earlier call would send the bot to the wrong meeting.
/// What was already on the clipboard when the app started has an unknown age, so it is never fresh.
public struct ClipboardAge {
    private var count: Int?
    private var changedAt: Date?
    public init() {}
    public mutating func observe(changeCount: Int, now: Date) {
        if let c = count, c != changeCount { changedAt = now }
        count = changeCount
    }
    public func isFresh(now: Date, within seconds: TimeInterval) -> Bool {
        guard let t = changedAt else { return false }
        return now.timeIntervalSince(t) <= seconds
    }
}
