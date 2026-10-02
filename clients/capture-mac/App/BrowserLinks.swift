import AppKit
import Foundation

/// Finds the join link of the call that just started: in the browsers' open tabs (the link was clicked a moment ago, and
/// Zoom's and Teams' launch pages stay open) and, failing that, on the clipboard (people copy the link to join — only one copied in the last few minutes).
///
/// Browsers are asked through AppleScript — Chrome-family browsers and Safari support it, Firefox does not. The first
/// time, macOS asks whether Vexa Capture may control each browser; declining only means that browser can't be searched.
/// Only the links that look like a meeting are kept; no other tab's address is stored or sent anywhere.
enum BrowserLinks {
    private enum Kind { case chrome, safari }
    private static let browsers: [(bundleID: String, name: String, kind: Kind)] = [
        ("com.google.Chrome", "Google Chrome", .chrome), ("company.thebrowser.Browser", "Arc", .chrome),
        ("com.brave.Browser", "Brave", .chrome), ("com.microsoft.edgemac", "Microsoft Edge", .chrome),
        ("com.vivaldi.Vivaldi", "Vivaldi", .chrome), ("com.apple.Safari", "Safari", .safari),
    ]

    struct Search {
        var candidates: [MeetingLink] = []
        var blocked: [String] = []          // browsers macOS would not let us read (Automation not allowed)
    }

    private static func script(_ b: (bundleID: String, name: String, kind: Kind), active: Bool) -> String {
        let tab = b.kind == .chrome ? "active tab" : "current tab"
        if active { return "tell application id \"\(b.bundleID)\" to return URL of \(tab) of front window" }
        return """
        tell application id "\(b.bundleID)"
            set out to {}
            repeat with w in windows
                repeat with t in tabs of w
                    set end of out to URL of t
                end repeat
            end repeat
            set AppleScript's text item delimiters to linefeed
            return out as text
        end tell
        """
    }

    /// Runs osascript as a child (a hung browser can't freeze this app) with a deadline.
    private static func run(_ source: String, timeout: TimeInterval = 4) -> (output: String, denied: Bool)? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", source]
        let out = Pipe(), err = Pipe()
        p.standardOutput = out; p.standardError = err
        do { try p.run() } catch { return nil }
        let deadline = Date().addingTimeInterval(timeout)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { p.terminate(); return nil }
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let errText = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        if p.terminationStatus != 0 { return ("", errText.contains("-1743")) }     // -1743: not authorized to send Apple events
        return (text, false)
    }

    enum Access { case allowed, denied, unknown }
    struct BrowserAccess { var name: String; var access: Access }

    /// For each browser that is open: can Vexa Capture read its tabs? Asking is what makes macOS show its permission prompt, so
    /// the setup check does it up front instead of in the middle of a call. Blocking (osascript): call off the main thread.
    static func access() -> (running: [BrowserAccess], notRunning: [String]) {
        let running = Set(NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier })
        var open: [BrowserAccess] = [], closed: [String] = []
        for b in browsers {
            guard NSWorkspace.shared.urlForApplication(withBundleIdentifier: b.bundleID) != nil else { continue }   // not installed
            guard running.contains(b.bundleID) else { closed.append(b.name); continue }
            if let r = run("tell application id \"\(b.bundleID)\" to return name", timeout: 30) {   // long: the person may be answering a prompt
                open.append(BrowserAccess(name: b.name, access: r.denied ? .denied : .allowed))
            } else { open.append(BrowserAccess(name: b.name, access: .unknown)) }
        }
        return (open, closed)
    }

    private static let clipboardFreshness: TimeInterval = 5 * 60
    private static var clipboardAge = ClipboardAge()
    /// Main thread. Called often, so a copy is dated by when it happened rather than by when the call was noticed.
    static func noteClipboard() { clipboardAge.observe(changeCount: NSPasteboard.general.changeCount, now: Date()) }

    /// Blocking (osascript): call off the main thread.
    static func search(for platform: CallPlatform?) -> Search {
        var all: [String] = [], active: [String] = [], blocked: [String] = []
        let running = Set(NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier })
        for b in browsers where running.contains(b.bundleID) {
            guard let tabs = run(script(b, active: false)) else { continue }
            if tabs.denied { blocked.append(b.name); continue }
            all += tabs.output.split(whereSeparator: \.isNewline).map(String.init)
            if let a = run(script(b, active: true)), !a.denied { active.append(a.output.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        // The clipboard is the last resort: a link someone copied a moment ago to paste into Zoom's "Join" box.
        var clip: [String] = []
        DispatchQueue.main.sync {
            noteClipboard()
            if clipboardAge.isFresh(now: Date(), within: clipboardFreshness), let s = NSPasteboard.general.string(forType: .string) { clip = [s] }
        }
        return Search(candidates: MeetingLinks.candidates(in: all + clip, preferred: active, platform: platform), blocked: blocked)
    }
}
