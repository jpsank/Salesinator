import Foundation

/// One line of the "is everything set up?" checklist.
public struct SetupItem: Equatable {
    public enum State { case ok, attention, info }
    public var name: String
    public var state: State
    public var detail: String
    public init(_ name: String, _ state: State, _ detail: String) { self.name = name; self.state = state; self.detail = detail }
}

public enum SetupReport {
    public static func format(_ items: [SetupItem]) -> String {
        items.map { i in
            let mark = i.state == .ok ? "✓" : i.state == .attention ? "✗" : "•"
            return "\(mark) \(i.name) — \(i.detail)"
        }.joined(separator: "\n")
    }
    public static func needsAttention(_ items: [SetupItem]) -> Bool { items.contains { $0.state == .attention } }
}

/// Vexa sites the person has already told this app to trust — by typing the address, confirming a pairing, or because the build
/// was made for it. A pairing link for one of those needs no second confirmation; for any other it still does, which is what stops
/// a link on some other site from pointing the app (and the calls it sends) somewhere new.
public enum TrustedBases {
    public static func contains(_ trusted: [String], _ base: URL) -> Bool {
        trusted.contains { ConnectLink.acceptableBase($0)?.absoluteString == base.absoluteString }
    }
    public static func adding(_ trusted: [String], _ base: URL) -> [String] {
        contains(trusted, base) ? trusted : trusted + [base.absoluteString]
    }
}

/// What this Mac lets Vexa Capture hear, in words for the setup window and the check. Hearing a call itself is how a call with no
/// findable link, or a bot that never got in, still gets captured — so a missing permission is something to fix, not a footnote.
public enum AudioAccess {
    public static func describe(microphone: Bool, screen: Bool) -> (ok: Bool, detail: String) {
        if microphone && screen { return (true, "On — if a call's link can't be found, its audio is captured on this Mac.") }
        var missing: [String] = []
        if !microphone { missing.append("Microphone") }
        if !screen { missing.append("Screen & System Audio Recording") }
        var detail = "Needs \(missing.joined(separator: " and ")) to capture a call's audio itself when its link can't be found — choose Allow."
        if !screen { detail += " If Vexa Capture is already switched on under Screen & System Audio Recording but this still says it needs it, switch it off and on again, then quit and reopen the app." }
        return (false, detail)
    }
}
