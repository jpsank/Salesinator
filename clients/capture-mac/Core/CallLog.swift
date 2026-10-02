import Foundation

/// What happened to a recent call, for the menu's "Recent calls" — so the person can see the app did (or didn't do) something
/// without opening the Vexa web app.
public struct CallRecord: Codable, Equatable {
    public enum Outcome: String, Codable { case botSent, botAlreadyThere, audioCaptured, skipped, noLink, failed }
    public var platform: String          // "Zoom" / "Microsoft Teams"
    public var when: Date
    public var outcome: Outcome
    public var note: String?
    public init(platform: String, when: Date, outcome: Outcome, note: String? = nil) {
        self.platform = platform; self.when = when; self.outcome = outcome; self.note = note
    }
}

public enum CallLog {
    public static func adding(_ log: [CallRecord], _ r: CallRecord, cap: Int = 5) -> [CallRecord] {
        Array(([r] + log).prefix(cap))
    }

    /// "Zoom · today 14:02 · Vexa's bot joined"
    public static func line(_ r: CallRecord, now: Date = Date(), calendar: Calendar = .current) -> String {
        let f = DateFormatter(); f.calendar = calendar; f.timeZone = calendar.timeZone; f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        let time = f.string(from: r.when)
        let day: String
        if calendar.isDate(r.when, inSameDayAs: now) { day = "today" }
        else if let y = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(r.when, inSameDayAs: y) { day = "yesterday" }
        else { f.dateFormat = "MMM d"; day = f.string(from: r.when) }
        return "\(r.platform) · \(day) \(time) · \(what(r))"
    }

    private static func what(_ r: CallRecord) -> String {
        switch r.outcome {
        case .botSent: return "Vexa's bot was sent"
        case .botAlreadyThere: return "a bot was already there"
        case .audioCaptured: return "audio captured on this Mac"
        case .skipped: return "skipped"
        case .noLink: return "no call link found"
        case .failed: return r.note.map { "couldn't join — \($0)" } ?? "couldn't join"
        }
    }
}
