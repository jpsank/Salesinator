import AppKit
import AVFoundation
import CoreGraphics
import ServiceManagement

/// The menu-bar app: notices a Zoom or Teams call, and either sends Vexa's bot to it (finding the call's link in the
/// browser) or, when there is no link, captures the call's audio here — and says so either way.
final class AppDelegate: NSObject, NSApplicationDelegate {
    private struct BotCall { let platform: CallPlatform; let serverPlatform: String; let nativeId: String; let since = Date(); var wasIn = false }

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let detector = CallDetector()
    private let settingsWindow = SettingsWindow()
    private let setupWindow = SetupWindow()
    private var timer: Timer?
    private var session: CaptureSession?          // audio captured on this Mac
    private var sessionState: CaptureSession.State = .stopped
    private var stalledNoted: String?           // the bot we already told the person about, so it is said once
    private var bot: BotCall?                     // a Vexa bot sent to the call
    private var searching = false                 // looking for the call's link / starting
    private var offered: CallPlatform?            // a call not handled automatically — waiting for the person
    private var announced = false
    private var ticks = 0
    private var checkingBot = false              // a look at whether the bot is still on the call is in flight
    private var update: UpdateOffer?              // a newer build the update feed offered

    func applicationDidFinishLaunching(_ n: Notification) {
        NSApp.setActivationPolicy(.accessory)
        settingsWindow.onSaved = { [weak self] in self?.rebuildMenu() }
        settingsWindow.onConnect = { [weak self] in self?.connect() }
        setupWindow.onChange = { [weak self] in self?.rebuildMenu() }
        setupWindow.onConnect = { [weak self] address in self?.connectToAddress(address) }
        rebuildMenu()
        if let shot = ProcessInfo.processInfo.environment["VEXA_CAPTURE_SHOT"] {      // dev: render the setup window to a PNG
            setupWindow.window?.appearance = NSAppearance(named: .aqua)         // the offscreen render has no dark backdrop
            setupWindow.present()
            DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                guard let v = self.setupWindow.window?.contentView, let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { print("no window to render"); exit(1) }
                v.cacheDisplay(in: v.bounds, to: rep)
                do { try rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: shot)); print("wrote", shot) }
                catch { print("write failed:", error); exit(1) }
                exit(0)
            }
            return
        }
        if ProcessInfo.processInfo.environment["VEXA_CAPTURE_SMOKE"] != nil {      // build check: no dialogs, no capture
            print("menu:", statusItem.menu?.items.map { $0.isSeparatorItem ? "—" : $0.title } ?? [])
            exit(0)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        if needsSetup { setupWindow.present() } else { Notifier.requestPermission() }       // the setup window asks for notifications itself
        checkForUpdate(announce: false)
    }

    // ── pairing with Vexa ──
    /// The pairing link the Vexa site opens: `vexacapture://connect?code=…&base=…`.
    func application(_ application: NSApplication, open urls: [URL]) {
        for u in urls { if let r = ConnectLink.parse(u) { confirmAndPair(r) } }
    }

    private func confirmAndPair(_ r: ConnectLink.Request) {
        // A site the person already approved (typed it, or confirmed it before, or this build was made for it) needs no second
        // confirmation: the click they just made in their own signed-in Vexa is the confirmation. Any other site still asks.
        if !TrustedBases.contains(Settings.trustedBases, r.base) {
            let a = NSAlert()
            a.messageText = "Connect Vexa Capture to \(r.base.host ?? r.base.absoluteString)?"
            a.informativeText = "Your calls — requests for Vexa's bot, or audio captured on this Mac — will be sent to this Vexa. Only continue if you just chose Connect in your own Vexa."
            a.addButton(withTitle: "Connect"); a.addButton(withTitle: "Cancel")
            NSApp.activate(ignoringOtherApps: true)
            guard a.runModal() == .alertFirstButtonReturn else { return }
            Settings.trust(r.base)
        }
        Task {
            let result: ConnectLink.Exchanged
            do {
                let (data, resp) = try await URLSession.shared.data(for: ConnectLink.exchangeRequest(r, device: Settings.device))
                result = ConnectLink.interpret(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data)
            } catch { result = .failed("Can't reach \(r.base.host ?? "Vexa"): \(error.localizedDescription)") }
            await MainActor.run {
                switch result {
                case .paired(let p):
                    Settings.apiKey = p.key; Settings.gatewayURL = p.api; Settings.serverURL = p.ingest
                    Settings.terminalURL = r.base.absoluteString; Settings.account = p.account
                    Notifier.post(title: "Vexa Capture is connected", body: p.account.isEmpty ? "Ready for your next call." : "Signed in as \(p.account). Ready for your next call.")
                    self.setupWindow.refresh(includeBrowsers: false)
                    self.openSetupIfAttention()
                case .failed(let why): self.problem(why)
                }
                self.rebuildMenu()
            }
        }
    }

    /// Opens the person's Vexa in the browser; its page opens this app back with a one-time code.
    @objc private func connect() {
        guard let page = ConnectLink.connectPage(base: Settings.terminalURL) else { openSetup(); return }
        NSWorkspace.shared.open(page)
    }

    /// Connect to the address typed in the setup window — typing it is the person's own choice of site.
    private func connectToAddress(_ address: String) {
        guard let base = ConnectLink.acceptableBase(address) else { return }
        Settings.terminalURL = base.absoluteString
        Settings.trust(base)
        connect()
    }

    // ── setup ──
    private var needsSetup: Bool { !Settings.consentAccepted || Settings.apiKey == nil }
    @objc private func openSetup() { setupWindow.present() }

    /// After a pairing: the setup window comes up only if something is still missing.
    private func openSetupIfAttention() {
        Task {
            let attention = SetupReport.needsAttention(await SetupCheck.run())
            await MainActor.run { if attention { self.setupWindow.present() } }
        }
    }

    @objc private func disconnect() {
        Settings.apiKey = nil; Settings.account = ""
        setupWindow.refresh(includeBrowsers: false)
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
        ticks += 1
        BrowserLinks.noteClipboard()
        if ticks % 4 == 0 { watchBot() }
        var activity = ProcessAudioProbe.activity()
        for p in CallPlatform.allCases where !Settings.isEnabled(p) { activity[p] = .idle }
        for event in detector.update(now: ProcessInfo.processInfo.systemUptime, activity: activity) {
            switch event {
            case .started(let p): callStarted(p)
            case .ended(let p): callEnded(p)
            }
        }
    }

    /// While a bot is on the call, ask Vexa every few seconds how it is doing. When the meeting ends — or someone removes the bot —
    /// it is gone within moments, well before the audio going quiet would say so. When it has not got into the call after a minute,
    /// the app stops waiting for it and captures the call's audio here instead. Only a clear answer counts; a failed or unreadable
    /// one leaves things as they are.
    private func watchBot() {
        guard let b = bot, !checkingBot, Date().timeIntervalSince(b.since) > 4,
              let key = Settings.apiKey else { return }
        checkingBot = true
        let gw = Settings.gatewayURL
        Task {
            let presence = await BotClient.running(gateway: gw, key: key)
            await MainActor.run {
                self.checkingBot = false
                guard let now = self.bot, now.nativeId == b.nativeId else { return }
                if !now.wasIn, BotRequest.isIn(presence, platform: b.serverPlatform, nativeId: b.nativeId) { self.bot?.wasIn = true; self.rebuildMenu() }
                if BotRequest.isGone(presence, platform: b.serverPlatform, nativeId: b.nativeId) {
                    if !now.wasIn, self.detector.isInCall(b.platform), self.session == nil {      // it ended without ever getting in — a link Zoom refused
                        self.botNeverJoined(b, key: key, stillRunning: false)
                        return
                    }
                    self.bot = nil
                    self.record(b.platform, .botLeft)
                    Notifier.post(title: "Vexa's bot left your \(b.platform.displayName) call", body: "It will be sent to your next call.")
                } else if BotRequest.hasStalled(presence, platform: b.serverPlatform, nativeId: b.nativeId, waited: Date().timeIntervalSince(b.since)) {
                    self.botNeverJoined(b, key: key, stillRunning: true)
                }
            }
        }
    }

    /// The bot never got into the call — it has been outside it too long (`stillRunning`), or it ended without ever being in (a link
    /// Zoom refused): hear the call from this Mac instead. When this Mac can't capture audio yet, a bot still trying is left to try
    /// and the person is told what would let the app help; one that has ended is just reported.
    private func botNeverJoined(_ b: BotCall, key: String, stillRunning: Bool) {
        guard detector.isInCall(b.platform), session == nil else { return }
        guard audioCaptureAvailable() else {
            guard stalledNoted != b.nativeId else { return }
            stalledNoted = b.nativeId
            if !stillRunning { bot = nil; record(b.platform, .botCouldntJoin) }
            offerAudioSetup(b.platform, headline: "Vexa's bot couldn't get into your \(b.platform.displayName) call",
                            why: "It never got in — the call may not accept it, or its link may be wrong.", paste: false) { [weak self] in
                guard stillRunning else { return }
                self?.bot = nil
                let gw = Settings.gatewayURL
                Task { _ = await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) }
            }
            return
        }
        bot = nil
        if stillRunning {
            let gw = Settings.gatewayURL
            Task { _ = await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) }
        }
        Notifier.post(title: "Vexa's bot couldn't get into your \(b.platform.displayName) call", body: "Capturing the call's audio on this Mac instead.")
        beginAudio(b.platform)
    }

    private func callStarted(_ p: CallPlatform) {
        guard !busy else { return }
        guard !needsSetup else {
            Notifier.post(title: "\(p.displayName) call detected", body: "Finish setting up Vexa Capture from its menu and it can help on your next call.")
            return
        }
        if Settings.autoCapture { handle(p) }
        else {
            offered = p
            Notifier.post(title: "\(p.displayName) call detected", body: "Open the Vexa Capture menu to send the bot, or skip it.")
            rebuildMenu()
        }
    }

    private func callEnded(_ p: CallPlatform) {
        if offered == p { offered = nil }
        if session?.platform == p { session?.stop(); session = nil; sessionState = .stopped }
        if let b = bot, b.platform == p, let key = Settings.apiKey {
            let gw = Settings.gatewayURL
            Task { _ = await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) }    // it leaves by itself too
            bot = nil
        }
        announced = false
        rebuildMenu()
    }

    // ── handling a call ──
    private func handle(_ p: CallPlatform) {
        guard let key = Settings.apiKey, !key.isEmpty else { openSetup(); return }
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
        record(p, .noLink)
        offerAudioSetup(p, headline: "Couldn't send the bot to your \(p.displayName) call", why: why, paste: true)
    }

    /// This Mac can't hear the call yet (Microphone or Screen Recording not allowed), so the audio fallback can't start. Say so and
    /// let the person allow it right now — the macOS prompts belong at the moment they matter — instead of leaving them a dead end.
    /// `paste` also offers sending the bot to a pasted link; `beforeCapture` runs first if they choose to allow.
    private func offerAudioSetup(_ p: CallPlatform, headline: String, why: String, paste: Bool, beforeCapture: (() -> Void)? = nil) {
        let a = NSAlert()
        a.messageText = headline
        a.informativeText = "\(why)\n\nAllow Vexa Capture to hear the call on this Mac and it captures the audio itself."
            + (paste ? " Or paste the call's link and Vexa's bot will join it." : "")
        a.addButton(withTitle: "Allow audio capture")
        if paste { a.addButton(withTitle: "Paste link…") }
        a.addButton(withTitle: "Not this time")
        NSApp.activate(ignoringOtherApps: true)
        let answer = a.runModal()
        if answer == .alertFirstButtonReturn { beforeCapture?(); beginAudio(p) }
        else if paste && answer == .alertSecondButtonReturn { pasteLink(p) }
        else { offered = p; rebuildMenu() }
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
                    self.record(p, .botSent)
                case .alreadyThere: self.record(p, .botAlreadyThere)
                case .keyRejected: self.setupWindow.present(); self.record(p, .failed, note: "Vexa didn't accept the saved key")
                default: self.record(p, .failed, note: BotRequest.explain(outcome, platform: p.displayName))
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
        guard let key = Settings.apiKey, !key.isEmpty else { openSetup(); return }
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
            self.record(p, .audioCaptured)
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
        a.informativeText = "macOS only lets an app hear another app's audio through this permission (no screen is recorded or kept). Turn on Vexa Capture under Privacy & Security → Screen & System Audio Recording — if it is already on, switch it off and on again — then quit and reopen Vexa Capture."
        a.addButton(withTitle: "Open System Settings"); a.addButton(withTitle: "Not now")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn,
           let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") { NSWorkspace.shared.open(url) }
        return false
    }

    private func problem(_ message: String) {
        let a = NSAlert(); a.messageText = "Vexa Capture"; a.informativeText = message
        NSApp.activate(ignoringOtherApps: true); a.runModal()
    }

    // ── updates ──
    /// Looks for a newer build — once a day on its own (quietly), or when asked from the menu (always with an answer).
    private func checkForUpdate(announce: Bool) {
        guard Updater.feed != nil else { if announce { problem("This build of Vexa Capture has no update address.") }; return }
        let last = Settings.lastUpdateCheck
        if !announce, let last, Date().timeIntervalSince(last) < 20 * 3600 { return }
        Task {
            let result = await Updater.check()
            await MainActor.run {
                if case .unavailable = result {} else { Settings.lastUpdateCheck = Date() }
                switch result {
                case .available(let o):
                    let isNew = self.update != o
                    self.update = o; self.rebuildMenu()
                    if announce { self.installUpdate() } else if isNew { Notifier.post(title: "Vexa Capture \(o.version) is available", body: "Choose Update from the menu-bar icon.") }
                case .upToDate: if announce { self.problem("Vexa Capture \(Updater.currentVersion) is the latest version.") }
                case .unavailable(let why): if announce { self.problem("Couldn't check for updates: \(why).") }
                }
            }
        }
    }

    @objc private func checkForUpdateFromMenu() { checkForUpdate(announce: true) }

    @objc private func installUpdate() {
        guard let o = update else { return }
        let a = NSAlert()
        a.messageText = "Vexa Capture \(o.version) is available"
        a.informativeText = "You have \(Updater.currentVersion). Download opens the new version's disk image — drag Vexa Capture onto Applications to replace this one. Your connection and settings stay."
        a.addButton(withTitle: "Download"); a.addButton(withTitle: "Later")
        NSApp.activate(ignoringOtherApps: true)
        guard a.runModal() == .alertFirstButtonReturn else { return }
        Task { if let why = await Updater.fetch(o) { await MainActor.run { self.problem("Couldn't get the update: \(why).") } } }
    }

    // ── menu ──
    private var state: AppState {
        if needsSetup { return .attention }
        if let s = session { if case .problem = sessionState { return .attention }; return s.isPaused ? .paused : .capturing }
        if let b = bot { return b.wasIn ? .bot : .working }       // the bot is only "on the call" once Vexa says it got in
        if searching || offered != nil { return .working }
        return .idle
    }

    private func record(_ p: CallPlatform, _ outcome: CallRecord.Outcome, note: String? = nil) {
        Settings.callLog = CallLog.adding(Settings.callLog, CallRecord(platform: p.displayName, when: Date(), outcome: outcome, note: note))
        rebuildMenu()
    }

    private func rebuildMenu() {
        let st = state
        statusItem.button?.image = st.image()
        statusItem.button?.toolTip = st.tooltip
        if st.image() == nil { statusItem.button?.title = "Vexa" }          // no SF Symbols: fall back to text
        let m = NSMenu()
        let status = NSMenuItem(title: statusLine(), action: nil, keyEquivalent: ""); status.isEnabled = false
        m.addItem(status)
        m.addItem(.separator())
        if needsSetup {
            m.addItem(item("Finish setting up…", #selector(openSetup)))
        } else if let s = session {
            if s.isPaused { m.addItem(item("Resume capturing", #selector(resume))) } else { m.addItem(item("Pause capturing", #selector(pause))) }
            m.addItem(item("Stop capturing this call", #selector(stopCapture)))
        } else if bot != nil {
            m.addItem(item("Remove the bot from this call", #selector(removeBot)))
        } else if let p = offered {
            m.addItem(item("Send the bot to this \(p.displayName) call", #selector(sendBotNow)))
            m.addItem(item("Capture its audio on this Mac instead", #selector(captureAudioNow)))
            m.addItem(item("Skip this call", #selector(skipCall)))
        }
        if m.items.last?.isSeparatorItem == false { m.addItem(.separator()) }

        let recent = NSMenu()
        let log = Settings.callLog
        if log.isEmpty { let none = NSMenuItem(title: "Nothing yet", action: nil, keyEquivalent: ""); none.isEnabled = false; recent.addItem(none) }
        for r in log { let i = NSMenuItem(title: CallLog.line(r), action: nil, keyEquivalent: ""); i.isEnabled = false; recent.addItem(i) }
        m.addItem(submenu("Recent calls", recent))

        let when = NSMenu()
        let botMode = item("Send Vexa's bot to the call", #selector(setBotMode)); botMode.state = Settings.mode == .bot ? .on : .off
        let audioMode = item("Capture audio on this Mac instead", #selector(setAudioMode)); audioMode.state = Settings.mode == .audio ? .on : .off
        let auto = item("Start without asking", #selector(toggleAuto)); auto.state = Settings.autoCapture ? .on : .off
        when.addItem(botMode); when.addItem(audioMode); when.addItem(.separator()); when.addItem(auto); when.addItem(.separator())
        for p in CallPlatform.allCases {
            let i = item("Watch for \(p.displayName) calls", #selector(togglePlatform(_:)))
            i.representedObject = p.rawValue; i.state = Settings.isEnabled(p) ? .on : .off
            when.addItem(i)
        }
        m.addItem(submenu("When a call starts", when))

        let prefs = NSMenu()
        if Settings.apiKey != nil, !Settings.account.isEmpty { let who = NSMenuItem(title: "Connected as \(Settings.account)", action: nil, keyEquivalent: ""); who.isEnabled = false; prefs.addItem(who) }
        prefs.addItem(item(Settings.apiKey == nil ? "Connect to Vexa…" : "Reconnect to Vexa…", #selector(connect)))
        if Settings.apiKey != nil { prefs.addItem(item("Disconnect", #selector(disconnect))) }
        let login = item("Open at login", #selector(toggleLogin)); login.state = opensAtLogin ? .on : .off
        prefs.addItem(login)
        prefs.addItem(.separator())
        prefs.addItem(item("Advanced settings…", #selector(openSettings)))
        if Updater.feed != nil { prefs.addItem(item("Check for updates…", #selector(checkForUpdateFromMenu))) }
        m.addItem(submenu("Preferences", prefs))

        m.addItem(.separator())
        if let u = update { m.addItem(item("Update to \(u.version)…", #selector(installUpdate))) }
        if !needsSetup { m.addItem(item("Set up Vexa Capture…", #selector(openSetup))) }
        m.addItem(item("Quit Vexa Capture", #selector(quit)))
        statusItem.menu = m
    }

    private func item(_ title: String, _ action: Selector) -> NSMenuItem { let i = NSMenuItem(title: title, action: action, keyEquivalent: ""); i.target = self; return i }
    private func submenu(_ title: String, _ menu: NSMenu) -> NSMenuItem { let i = NSMenuItem(title: title, action: nil, keyEquivalent: ""); i.submenu = menu; return i }

    private func statusLine() -> String {
        if !Settings.consentAccepted { return "Finish setting up to get started" }
        if Settings.apiKey == nil { return "Not connected to Vexa" }
        if let b = bot { return b.wasIn ? "Vexa's bot is on your \(b.platform.displayName) call" : "Vexa's bot is joining your \(b.platform.displayName) call…" }
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
        if searching { return "Setting up your call…" }
        if let p = offered { return "\(p.displayName) call detected" }
        return "Ready — watching for Zoom and Teams calls"
    }

    /// Leave this call alone: nothing is sent or captured, and nothing more is offered until the next call.
    @objc private func skipCall() {
        guard let p = offered else { return }
        offered = nil
        record(p, .skipped)
    }

    @objc private func pause() { session?.pause(); rebuildMenu() }
    @objc private func resume() { session?.resume(); rebuildMenu() }
    @objc private func stopCapture() { session?.stop(); session = nil; sessionState = .stopped; rebuildMenu() }
    @objc private func removeBot() {
        guard let b = bot, let key = Settings.apiKey else { return }
        let gw = Settings.gatewayURL
        bot = nil; rebuildMenu()
        Task {
            switch await BotClient.stop(platform: b.serverPlatform, nativeId: b.nativeId, gateway: gw, key: key) {
            case .removed: break
            case .keyRejected: await MainActor.run { self.setupWindow.present() }
            case .failed(let why): await MainActor.run { self.problem("Couldn't remove the bot: \(why). It leaves by itself when the call ends.") }
            }
        }
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
