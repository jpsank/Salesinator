import Foundation

public enum CallPlatform: String, CaseIterable {
    case zoom, teams
    /// The macOS bundle ids whose audio activity says a call is on. Zoom runs a second helper process; Teams has a
    /// classic and a "new Teams" app.
    public var bundleIDs: [String] {
        switch self {
        case .zoom: return ["us.zoom.xos", "us.zoom.caphost"]
        case .teams: return ["com.microsoft.teams2", "com.microsoft.teams"]
        }
    }
    public var displayName: String { self == .zoom ? "Zoom" : "Microsoft Teams" }
}

/// What the system says one platform's app is doing with audio right now.
public struct AudioActivity: Equatable {
    public var input: Bool      // the app is capturing the microphone
    public var output: Bool     // the app is playing audio
    public init(input: Bool, output: Bool) { self.input = input; self.output = output }
    public static let idle = AudioActivity(input: false, output: false)
}

public enum CallEvent: Equatable {
    case started(CallPlatform)
    case ended(CallPlatform)
}

/// Turns a stream of audio-activity samples into call start/end events.
///
/// A call STARTS once the app has held the microphone for ``startAfter`` seconds (a settings test or a notification
/// chime is shorter, and playing audio alone — a recording, a join sound — never starts one). It ENDS once the app has
/// used neither the microphone nor the speaker for ``endAfter`` seconds: long enough that muting, or a quiet stretch
/// where Zoom lets go of the mic, does not split one call into two.
public final class CallDetector {
    private struct Track { var active = false; var since: TimeInterval? ; var idleSince: TimeInterval? }
    private var tracks: [CallPlatform: Track] = [:]
    private let startAfter: TimeInterval
    private let endAfter: TimeInterval

    public init(startAfter: TimeInterval = 3, endAfter: TimeInterval = 20) {
        self.startAfter = startAfter
        self.endAfter = endAfter
    }

    public func isInCall(_ p: CallPlatform) -> Bool { tracks[p]?.active ?? false }

    public func update(now: TimeInterval, activity: [CallPlatform: AudioActivity]) -> [CallEvent] {
        var events: [CallEvent] = []
        for platform in CallPlatform.allCases {
            let a = activity[platform] ?? .idle
            var t = tracks[platform] ?? Track()
            if !t.active {
                if a.input {
                    if t.since == nil { t.since = now }
                    if now - (t.since ?? now) >= startAfter { t.active = true; t.idleSince = nil; events.append(.started(platform)) }
                } else { t.since = nil }
            } else {
                if a.input || a.output { t.idleSince = nil }
                else {
                    if t.idleSince == nil { t.idleSince = now }
                    if now - (t.idleSince ?? now) >= endAfter { t.active = false; t.since = nil; t.idleSince = nil; events.append(.ended(platform)) }
                }
            }
            tracks[platform] = t
        }
        return events
    }
}
