import AppKit
import AVFoundation
import CoreGraphics

/// The menu-bar app: watches Zoom and Teams for a call (by their audio activity), captures it, and says so.
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let detector = CallDetector()
    private let settingsWindow = SettingsWindow()
    private var timer: Timer?
    private var session: CaptureSession?
    private var sessionState: CaptureSession.State = .stopped
    private var offered: CallPlatform?            // a call detected while auto-capture is off
    private var starting = false

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem.button?.title = "Vexa"
        settingsWindow.onSaved = { [weak self] in self?.rebuildMenu() }
        rebuildMenu()
        if ProcessInfo.processInfo.environment["VEXA_CAPTURE_SMOKE"] != nil {      // build check: no dialogs, no capture
            print("menu:", statusItem.menu?.items.map { $0.isSeparatorItem ? "—" : $0.title } ?? [])
            exit(0)
        }
        Notifier.requestPermission()
        if !Settings.consentAccepted { askConsent() }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        if Settings.apiKey == nil { settingsWindow.present() }
    }

    // ── detecting ──
    private func tick() {
        var activity = ProcessAudioProbe.activity()
        for p in CallPlatform.allCases where !Settings.isEnabled(p) { activity[p] = .idle }
        for event in detector.update(now: ProcessInfo.processInfo.systemUptime, activity: activity) {
            switch event {
            case .started(let p): callStarted(p)
            case .ended(let p): callEnded(p)
            }
        }
    }

    private func callStarted(_ p: CallPlatform) {
        guard Settings.consentAccepted, session == nil, !starting else { return }
        if Settings.autoCapture { beginCapture(p) }
        else {
            offered = p
            Notifier.post(title: "\(p.displayName) call detected", body: "Open the Vexa menu and choose “Capture this call” to transcribe it.")
            rebuildMenu()
        }
    }

    private func callEnded(_ p: CallPlatform) {
        if offered == p { offered = nil }
        if session?.platform == p { session?.stop(); session = nil; sessionState = .stopped }
        rebuildMenu()
    }

    // ── capturing ──
    @objc private func captureOffered() { if let p = offered { beginCapture(p) } }

    private func beginCapture(_ p: CallPlatform) {
        guard let key = Settings.apiKey, !key.isEmpty else { settingsWindow.present(); return }
        starting = true; offered = nil
        ensureMicrophone { [weak self] micOK in
            guard let self else { return }
            guard micOK else { self.starting = false; self.problem("Vexa Capture needs the Microphone permission to hear you. Allow it under System Settings → Privacy & Security → Microphone."); return }
            guard self.ensureScreenRecording() else { self.starting = false; return }
            guard let s = CaptureSession(platform: p, serverURL: Settings.serverURL, apiKey: key) else {
                self.starting = false; self.problem("The server address in Settings isn't a valid ws:// or wss:// URL."); return
            }
            s.onState = { [weak self] st in DispatchQueue.main.async { self?.sessionChanged(s, st) } }
            self.session = s; self.starting = false
            s.start()
        }
    }

    private func sessionChanged(_ s: CaptureSession, _ st: CaptureSession.State) {
        guard s === session else { return }
        sessionState = st
        switch st {
        case .capturing where !announced:
            announced = true
            Notifier.post(title: "Vexa is transcribing your \(s.platform.displayName) call",
                          body: "Let the others on the call know. Pause or stop from the menu bar.")
        case .problem(let m): problem(m); session = nil; announced = false
        case .stopped: if session === s { session = nil }; announced = false
        default: break
        }
        rebuildMenu()
    }
    private var announced = false

    // ── permissions ──
    private func ensureMicrophone(_ done: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: done(true)
        case .notDetermined: AVCaptureDevice.requestAccess(for: .audio) { ok in DispatchQueue.main.async { done(ok) } }
        default: done(false)
        }
    }

    private func ensureScreenRecording() -> Bool {
        if CGPreflightScreenCaptureAccess() { return true }
        CGRequestScreenCaptureAccess()
        let a = NSAlert()
        a.messageText = "Allow Screen & System Audio Recording"
        a.informativeText = "macOS only lets an app hear another app's audio through this permission (no screen is recorded or kept). Turn on Vexa Capture under Privacy & Security → Screen & System Audio Recording, then quit and reopen Vexa Capture."
        a.addButton(withTitle: "Open System Settings"); a.addButton(withTitle: "Not now")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn,
           let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") { NSWorkspace.shared.open(url) }
        return false
    }

    private func askConsent() {
        let a = NSAlert()
        a.messageText = "Before Vexa Capture listens"
        a.informativeText = "When you are on a Zoom or Microsoft Teams call, Vexa Capture sends that call's audio — what the others say, and your microphone — to your Vexa server to be transcribed.\n\nPeople on the call are not told automatically. Many places require everyone's consent to record or transcribe a conversation, so tell them."
        a.addButton(withTitle: "I'll tell people on my calls"); a.addButton(withTitle: "Quit")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn { Settings.consentAccepted = true; rebuildMenu() } else { NSApp.terminate(nil) }
    }

    private func problem(_ message: String) {
        let a = NSAlert(); a.messageText = "Vexa Capture"; a.informativeText = message
        NSApp.activate(ignoringOtherApps: true); a.runModal()
    }

    // ── menu ──
    private func rebuildMenu() {
        let capturing = session != nil
        statusItem.button?.title = capturing ? (session?.isPaused == true ? "Ⅱ Vexa" : "● Vexa") : "Vexa"
        let m = NSMenu()
        let status = NSMenuItem(title: statusLine(), action: nil, keyEquivalent: ""); status.isEnabled = false
        m.addItem(status)
        m.addItem(.separator())
        if let s = session {
            if s.isPaused { m.addItem(item("Resume capturing", #selector(resume))) } else { m.addItem(item("Pause capturing", #selector(pause))) }
            m.addItem(item("Stop capturing this call", #selector(stopCapture)))
        } else if let p = offered {
            m.addItem(item("Capture this \(p.displayName) call", #selector(captureOffered)))
        }
        m.addItem(.separator())
        for p in CallPlatform.allCases {
            let i = item("Capture \(p.displayName) calls", #selector(togglePlatform(_:)))
            i.representedObject = p.rawValue; i.state = Settings.isEnabled(p) ? .on : .off
            m.addItem(i)
        }
        let auto = item("Capture calls automatically", #selector(toggleAuto)); auto.state = Settings.autoCapture ? .on : .off
        m.addItem(auto)
        m.addItem(.separator())
        m.addItem(item("Settings…", #selector(openSettings)))
        m.addItem(item("Quit Vexa Capture", #selector(quit)))
        statusItem.menu = m
    }

    private func item(_ title: String, _ action: Selector) -> NSMenuItem { let i = NSMenuItem(title: title, action: action, keyEquivalent: ""); i.target = self; return i }

    private func statusLine() -> String {
        if !Settings.consentAccepted { return "Waiting for your acknowledgement" }
        guard let s = session else {
            if let p = offered { return "\(p.displayName) call detected — not capturing" }
            return Settings.apiKey == nil ? "Add your API key in Settings" : "Watching for Zoom and Teams calls"
        }
        switch sessionState {
        case .connecting: return "Connecting to Vexa…"
        case .capturing: return "Capturing your \(s.platform.displayName) call"
        case .paused: return "Paused — not capturing"
        case .reconnecting: return "Reconnecting to Vexa…"
        case .problem(let m): return m
        case .stopped: return "Stopped"
        }
    }

    @objc private func pause() { session?.pause(); rebuildMenu() }
    @objc private func resume() { session?.resume(); rebuildMenu() }
    @objc private func stopCapture() { session?.stop(); session = nil; sessionState = .stopped; rebuildMenu() }
    @objc private func togglePlatform(_ i: NSMenuItem) {
        guard let raw = i.representedObject as? String, let p = CallPlatform(rawValue: raw) else { return }
        Settings.setEnabled(p, !Settings.isEnabled(p)); rebuildMenu()
    }
    @objc private func toggleAuto() { Settings.autoCapture.toggle(); rebuildMenu() }
    @objc private func openSettings() { settingsWindow.present() }
    @objc private func quit() { session?.stop(); NSApp.terminate(nil) }
}
