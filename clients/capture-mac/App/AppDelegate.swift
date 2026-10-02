import AppKit
import AVFoundation
import CoreGraphics
import ServiceManagement

/// The menu-bar app: notices a Zoom or Teams call, and either sends Vexa's bot to it (finding the call's link in the
/// browser) or, when there is no link, captures the call's audio here — and says so either way.
final class AppDelegate: NSObject, NSApplicationDelegate {
    private struct BotCall { let platform: CallPlatform; let serverPlatform: String; let nativeId: String }

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let detector = CallDetector()
    private let settingsWindow = SettingsWindow()
    private var timer: Timer?
    private var session: CaptureSession?          // audio captured on this Mac
    private var sessionState: CaptureSession.State = .stopped
    private var bot: BotCall?                     // a Vexa bot sent to the call
    private var searching = false                 // looking for the call's link / starting
    private var offered: CallPlatform?            // a call not handled automatically — waiting for the person
    private var announced = false

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem.button?.title = "Vexa"
        settingsWindow.onSaved = { [weak self] in self?.rebuildMenu() }
        settingsWindow.onConnect = { [weak self] in self?.connect() }
        rebuildMenu()
        if ProcessInfo.processInfo.environment["VEXA_CAPTURE_SMOKE"] != nil {      // build check: no dialogs, no capture
            print("menu:", statusItem.menu?.items.map { $0.isSeparatorItem ? "—" : $0.title } ?? [])
            exit(0)
        }
        Notifier.requestPermission()
        if !Settings.consentAccepted { askConsent() }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        if Settings.apiKey == nil { askAddressAndConnect() }
    }

    // ── pairing with Vexa ──
    /// The pairing link the Vexa site opens: `vexacapture://connect?code=…&base=…`.
    func application(_ application: NSApplication, open urls: [URL]) {
        for u in urls { if let r = ConnectLink.parse(u) { confirmAndPair(r) } }
    }

    private func confirmAndPair(_ r: ConnectLink.Request) {
        let a = NSAlert()
        a.messageText = "Connect Vexa Capture to \(r.base.host ?? r.base.absoluteString)?"
        a.informativeText = "Your calls — requests for Vexa's bot, or audio captured on this Mac — will be sent to this Vexa. Only continue if you just chose Connect in your own Vexa."
        a.addButton(withTitle: "Connect"); a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        Task {
            let result: ConnectLink.Exchanged
            do {
                let (data, resp) = try await URLSession.shared.data(for: ConnectLink.exchangeRequest(r))
                result = ConnectLink.interpret(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data)
            } catch { result = .failed("Can't reach \(r.base.host ?? "Vexa"): \(error.localizedDescription)") }
            await MainActor.run {
                switch result {
                case .paired(let p):
                    Settings.apiKey = p.key; Settings.gatewayURL = p.api; Settings.serverURL = p.ingest
                    Settings.terminalURL = r.base.absoluteString; Settings.account = p.account
                    Notifier.post(title: "Vexa Capture is connected", body: p.account.isEmpty ? "Ready for your next call." : "Signed in as \(p.account). Ready for your next call.")
                case .failed(let why): self.problem(why)
                }
                self.rebuildMenu()
            }
        }
    }

    /// Opens the person's Vexa in the browser; its page opens this app back with a one-time code.
    @objc private func connect() {
        guard let page = ConnectLink.connectPage(base: Settings.terminalURL) else { askAddressAndConnect(); return }
        NSWorkspace.shared.open(page)
    }

    private func askAddressAndConnect() {
        let a = NSAlert()
        a.messageText = "Connect to Vexa"
        a.informativeText = "Enter the address you open Vexa at. Your browser will open to confirm — no token to copy."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 24))
        field.stringValue = Settings.terminalURL
        a.accessoryView = field
        a.addButton(withTitle: "Connect"); a.addButton(withTitle: "Enter details by hand…"); a.addButton(withTitle: "Later")
        NSApp.activate(ignoringOtherApps: true)
        switch a.runModal() {
        case .alertFirstButtonReturn:
            guard let base = ConnectLink.acceptableBase(field.stringValue) else { problem("That isn't an https:// address (or http://localhost)."); return }
            Settings.terminalURL = base.absoluteString
            connect()
        case .alertSecondButtonReturn: settingsWindow.present()
        default: break
        }
    }

    @objc private func disconnect() {
        Settings.apiKey = nil; Settings.account = ""
        rebuildMenu()
    }

    // ── start at login ──
    private var opensAtLogin: Bool { SMAppService.mainApp.status == .enabled }
    @objc private func toggleLogin() {
        do { if opensAtLogin { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() } }
        catch { problem("Couldn't change Open at login: \(error.localizedDescription). Try moving Vexa Capture into Applications first.") }
        rebuildMenu()
    }

    private var busy: Bool { session != nil || bot != nil || searching }

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
        guard Settings.consentAccepted, !busy else { return }
        if Settings.autoCapture { handle(p) }
        else {
            offered = p
            Notifier.post(title: "\(p.displayName) call detected", body: "Open the Vexa menu to send the bot or capture it.")
            rebuildMenu()
        }
    }

    private func callEnded(_ p: CallPlatform) {
        if offered == p { offered = nil }
        if session?.platform == p { session?.stop(); session = nil; sessionState = .stopped }
        if let b = bot, b.platform == p, let key = Settings.apiKey {
            let gw = Settings.gatewayURL
            Task { await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) }    // it leaves by itself too
            bot = nil
        }
        announced = false
        rebuildMenu()
    }

    // ── handling a call ──
    private func handle(_ p: CallPlatform) {
        guard let key = Settings.apiKey, !key.isEmpty else { askAddressAndConnect(); return }
        offered = nil
        if Settings.mode == .audio { beginAudio(p); return }
        searching = true; rebuildMenu()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var found = BrowserLinks.Search()
            for attempt in 0..<3 {                                  // the browser may take a moment to show the launch page
                found = BrowserLinks.search(for: p)
                if !found.candidates.isEmpty || attempt == 2 { break }
                Thread.sleep(forTimeInterval: 3)
            }
            DispatchQueue.main.async { self?.searching = false; self?.linkSearchDone(p, found) }
        }
    }

    private func linkSearchDone(_ p: CallPlatform, _ found: BrowserLinks.Search) {
        guard detector.isInCall(p) else { rebuildMenu(); return }                // the call ended while we were looking
        switch found.candidates.count {
        case 0: noLink(p, blocked: found.blocked)
        case 1: sendBot(p, found.candidates[0])
        default: if let l = choose(among: found.candidates, for: p) { sendBot(p, l) } else { offered = p; rebuildMenu() }
        }
    }

    private func noLink(_ p: CallPlatform, blocked: [String]) {
        let why = blocked.isEmpty ? "I couldn't find this call's link." : "I couldn't read \(blocked.joined(separator: ", ")) to find this call's link — allow Vexa Capture to control it under System Settings → Privacy & Security → Automation."
        if audioCaptureAvailable() {
            Notifier.post(title: "No call link found", body: "\(why) Capturing the call's audio on this Mac instead.")
            beginAudio(p)
            return
        }
        let a = NSAlert()
        a.messageText = "Couldn't send the bot to your \(p.displayName) call"
        a.informativeText = "\(why)\n\nPaste the call's link and Vexa's bot will join it."
        a.addButton(withTitle: "Paste link…"); a.addButton(withTitle: "Not this time")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn { pasteLink(p) } else { offered = p; rebuildMenu() }
    }

    private func pasteLink(_ p: CallPlatform) {
        let a = NSAlert()
        a.messageText = "Paste your \(p.displayName) call's link"
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        if let clip = NSPasteboard.general.string(forType: .string), MeetingLinks.classify(clip) != nil { field.stringValue = clip }
        a.accessoryView = field
        a.addButton(withTitle: "Send the bot"); a.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { offered = p; rebuildMenu(); return }
        if let link = MeetingLinks.classify(field.stringValue) { sendBot(p, link) }
        else { problem("That isn't a Zoom, Teams or Google Meet join link."); offered = p; rebuildMenu() }
    }

    private func choose(among links: [MeetingLink], for p: CallPlatform) -> MeetingLink? {
        let a = NSAlert()
        a.messageText = "Which \(p.displayName) call are you on?"
        a.informativeText = "More than one meeting link is open in your browser."
        let popup = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 420, height: 26))
        popup.addItems(withTitles: links.map { $0.url })
        a.accessoryView = popup
        a.addButton(withTitle: "Send the bot"); a.addButton(withTitle: "Not this time")
        NSApp.activate(ignoringOtherApps: true)
        return a.runModal() == .alertFirstButtonReturn ? links[popup.indexOfSelectedItem] : nil
    }

    private func sendBot(_ p: CallPlatform, _ link: MeetingLink) {
        guard let key = Settings.apiKey else { return }
        searching = true; rebuildMenu()
        let gw = Settings.gatewayURL
        Task {
            let outcome = await BotClient.send(to: link, gateway: gw, key: key)
            await MainActor.run {
                self.searching = false
                switch outcome {
                case .sent(let platform, let id):
                    self.bot = BotCall(platform: p, serverPlatform: platform.isEmpty ? link.platform.rawValue : platform, nativeId: id)
                case .keyRejected: self.settingsWindow.present()
                default: break
                }
                Notifier.post(title: "Vexa", body: BotRequest.explain(outcome, platform: p.displayName))
                if case .sent = outcome {} else if case .alreadyThere = outcome {} else { self.offered = p }
                self.rebuildMenu()
            }
        }
    }

    // ── capturing audio on this Mac ──
    @objc private func sendBotNow() { if let p = offered { handle(p) } }
    @objc private func captureAudioNow() { if let p = offered { beginAudio(p) } }

    private func audioCaptureAvailable() -> Bool {
        AVCaptureDevice.authorizationStatus(for: .audio) == .authorized && CGPreflightScreenCaptureAccess()
    }

    private func beginAudio(_ p: CallPlatform) {
        guard let key = Settings.apiKey, !key.isEmpty else { settingsWindow.present(); return }
        searching = true; offered = nil; rebuildMenu()
        ensureMicrophone { [weak self] micOK in
            guard let self else { return }
            guard micOK else { self.searching = false; self.problem("Vexa Capture needs the Microphone permission to hear you. Allow it under System Settings → Privacy & Security → Microphone."); return }
            guard self.ensureScreenRecording() else { self.searching = false; return }
            guard let s = CaptureSession(platform: p, serverURL: Settings.serverURL, apiKey: key) else {
                self.searching = false; self.problem("The audio capture address in Settings isn't a valid ws:// or wss:// URL."); return
            }
            s.onState = { [weak self] st in DispatchQueue.main.async { self?.sessionChanged(s, st) } }
            self.session = s; self.searching = false
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
        a.messageText = "Before Vexa Capture acts on your calls"
        a.informativeText = "When you are on a Zoom or Microsoft Teams call, Vexa Capture either sends Vexa's bot to it — a visible participant — or, if it can't find the call's link, captures the call's audio and your microphone on this Mac and sends them to your Vexa server to be transcribed.\n\nPeople on the call are not told automatically about an audio capture. Many places require everyone's consent to record or transcribe a conversation, so tell them."
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
        statusItem.button?.title = session != nil ? (session?.isPaused == true ? "Ⅱ Vexa" : "● Vexa") : (bot != nil ? "◉ Vexa" : "Vexa")
        let m = NSMenu()
        let status = NSMenuItem(title: statusLine(), action: nil, keyEquivalent: ""); status.isEnabled = false
        m.addItem(status)
        if Settings.apiKey != nil, !Settings.account.isEmpty {
            let who = NSMenuItem(title: "Connected as \(Settings.account)", action: nil, keyEquivalent: ""); who.isEnabled = false
            m.addItem(who)
        }
        m.addItem(.separator())
        if let s = session {
            if s.isPaused { m.addItem(item("Resume capturing", #selector(resume))) } else { m.addItem(item("Pause capturing", #selector(pause))) }
            m.addItem(item("Stop capturing this call", #selector(stopCapture)))
        } else if bot != nil {
            m.addItem(item("Remove the bot from this call", #selector(removeBot)))
        } else if let p = offered {
            m.addItem(item("Send the bot to this \(p.displayName) call", #selector(sendBotNow)))
            m.addItem(item("Capture this \(p.displayName) call's audio on this Mac", #selector(captureAudioNow)))
        }
        m.addItem(.separator())
        let botMode = item("Send a bot to the call", #selector(setBotMode)); botMode.state = Settings.mode == .bot ? .on : .off
        let audioMode = item("Capture audio on this Mac", #selector(setAudioMode)); audioMode.state = Settings.mode == .audio ? .on : .off
        m.addItem(botMode); m.addItem(audioMode)
        m.addItem(.separator())
        for p in CallPlatform.allCases {
            let i = item("Watch for \(p.displayName) calls", #selector(togglePlatform(_:)))
            i.representedObject = p.rawValue; i.state = Settings.isEnabled(p) ? .on : .off
            m.addItem(i)
        }
        let auto = item("Act on calls automatically", #selector(toggleAuto)); auto.state = Settings.autoCapture ? .on : .off
        m.addItem(auto)
        m.addItem(.separator())
        m.addItem(item(Settings.apiKey == nil ? "Connect to Vexa…" : "Reconnect to Vexa…", #selector(connect)))
        if Settings.apiKey != nil { m.addItem(item("Disconnect", #selector(disconnect))) }
        let login = item("Open at login", #selector(toggleLogin)); login.state = opensAtLogin ? .on : .off
        m.addItem(login)
        m.addItem(item("Settings…", #selector(openSettings)))
        m.addItem(item("Quit Vexa Capture", #selector(quit)))
        statusItem.menu = m
    }

    private func item(_ title: String, _ action: Selector) -> NSMenuItem { let i = NSMenuItem(title: title, action: action, keyEquivalent: ""); i.target = self; return i }

    private func statusLine() -> String {
        if !Settings.consentAccepted { return "Waiting for your acknowledgement" }
        if let b = bot { return "A Vexa bot is on your \(b.platform.displayName) call" }
        if let s = session {
            switch sessionState {
            case .connecting: return "Connecting to Vexa…"
            case .capturing: return "Capturing your \(s.platform.displayName) call"
            case .paused: return "Paused — not capturing"
            case .reconnecting: return "Reconnecting to Vexa…"
            case .problem(let m): return m
            case .stopped: return "Stopped"
            }
        }
        if searching { return "Working on your call…" }
        if let p = offered { return "\(p.displayName) call detected — not handled yet" }
        return Settings.apiKey == nil ? "Not connected — choose Connect to Vexa" : "Watching for Zoom and Teams calls"
    }

    @objc private func pause() { session?.pause(); rebuildMenu() }
    @objc private func resume() { session?.resume(); rebuildMenu() }
    @objc private func stopCapture() { session?.stop(); session = nil; sessionState = .stopped; rebuildMenu() }
    @objc private func removeBot() {
        guard let b = bot, let key = Settings.apiKey else { return }
        let gw = Settings.gatewayURL
        bot = nil; rebuildMenu()
        Task { await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) }
    }
    @objc private func setBotMode() { Settings.mode = .bot; rebuildMenu() }
    @objc private func setAudioMode() { Settings.mode = .audio; rebuildMenu() }
    @objc private func togglePlatform(_ i: NSMenuItem) {
        guard let raw = i.representedObject as? String, let p = CallPlatform(rawValue: raw) else { return }
        Settings.setEnabled(p, !Settings.isEnabled(p)); rebuildMenu()
    }
    @objc private func toggleAuto() { Settings.autoCapture.toggle(); rebuildMenu() }
    @objc private func openSettings() { settingsWindow.present() }
    @objc private func quit() { session?.stop(); NSApp.terminate(nil) }
}
