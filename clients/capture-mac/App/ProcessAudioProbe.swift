import CoreAudio
import Foundation

/// Which call apps are using audio right now, read from CoreAudio's per-process view (what System Settings'
/// orange microphone dot is drawn from). No permission is needed to ask. The property selectors are four-character
/// codes spelled out here because the Command Line Tools SDK predates the named constants.
enum ProcessAudioProbe {
    private static func fourCC(_ s: String) -> UInt32 { s.unicodeScalars.reduce(0) { ($0 << 8) | $1.value } }
    private static let processList = fourCC("prs#"), bundleID = fourCC("pbid"), runningInput = fourCC("piri"), runningOutput = fourCC("piro")

    private static func address(_ selector: UInt32) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    }

    private static func processObjects() -> [AudioObjectID] {
        var a = address(processList)
        var size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &a, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    private static func flag(_ id: AudioObjectID, _ selector: UInt32) -> Bool {
        var a = address(selector); var v: UInt32 = 0; var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(id, &a, 0, nil, &size, &v) == noErr && v != 0
    }

    private static func bundle(_ id: AudioObjectID) -> String? {
        var a = address(bundleID); var v: Unmanaged<CFString>?; var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &v) == noErr, let cf = v?.takeRetainedValue() else { return nil }
        return cf as String
    }

    /// One reading per platform: input/output is true if ANY of that platform's processes has it running.
    static func activity() -> [CallPlatform: AudioActivity] {
        var byBundle: [String: AudioActivity] = [:]
        for id in processObjects() {
            guard let b = bundle(id) else { continue }
            let now = AudioActivity(input: flag(id, runningInput), output: flag(id, runningOutput))
            let prev = byBundle[b] ?? .idle
            byBundle[b] = AudioActivity(input: prev.input || now.input, output: prev.output || now.output)
        }
        var out: [CallPlatform: AudioActivity] = [:]
        for p in CallPlatform.allCases {
            let readings = p.bundleIDs.compactMap { byBundle[$0] }
            out[p] = AudioActivity(input: readings.contains { $0.input }, output: readings.contains { $0.output })
        }
        return out
    }
}
