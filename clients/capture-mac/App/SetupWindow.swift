import AppKit
import ServiceManagement
import UserNotifications

/// One window for everything that has to be true before Vexa Capture can help on a call, each as a row that says where it stands and
/// has the one button that fixes it. It replaces a string of dialogs on first run, and is what "Set up Vexa Capture…" in the menu opens.
final class SetupWindow: NSWindowController, NSWindowDelegate {
    var onConnect: ((String) -> Void)?
    var onChange: (() -> Void)?

    private let consent = NSButton(checkboxWithTitle: "I'll let the people on my calls know", target: nil, action: nil)
    private let address = NSTextField()
    private let connectButton = NSButton(title: "Connect", target: nil, action: nil)
    private let browserButton = NSButton(title: "Allow access", target: nil, action: nil)
    private let notifyButton = NSButton(title: "Turn on", target: nil, action: nil)
    private let login = NSButton(checkboxWithTitle: "Open Vexa Capture when I log in", target: nil, action: nil)
    private let done = NSButton(title: "Done", target: nil, action: nil)
    private var rows: [String: (icon: NSImageView, detail: NSTextField)] = [:]
    private var snapshot: SetupSnapshot?
    private var timer: Timer?

    convenience init() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 520), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "Set up Vexa Capture"
        w.isReleasedWhenClosed = false
        self.init(window: w)
        w.delegate = self
        build(into: w.contentView!)
    }

    // ── layout ──
    private func label(_ s: String, size: CGFloat = 12, weight: NSFont.Weight = .regular, color: NSColor = .labelColor) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: s); l.font = .systemFont(ofSize: size, weight: weight); l.textColor = color
        l.maximumNumberOfLines = 0; return l
    }

    private func row(_ id: String, title: String, detail: String, control: NSView?) -> NSView {
        let icon = NSImageView(); icon.imageScaling = .scaleProportionallyDown
        icon.widthAnchor.constraint(equalToConstant: 22).isActive = true; icon.heightAnchor.constraint(equalToConstant: 22).isActive = true
        let t = label(title, size: 13, weight: .semibold)
        let d = label(detail, size: 11.5, color: .secondaryLabelColor)
        rows[id] = (icon, d)
        let text = NSStackView(views: [t, d] + (control.map { [$0] } ?? [])); text.orientation = .vertical; text.alignment = .leading; text.spacing = 4
        let r = NSStackView(views: [icon, text]); r.orientation = .horizontal; r.alignment = .top; r.spacing = 12
        r.translatesAutoresizingMaskIntoConstraints = false
        return r
    }

    private func build(into v: NSView) {
        consent.target = self; consent.action = #selector(consentToggled)
        address.placeholderString = "https://terminal.your-company.com"
        address.widthAnchor.constraint(equalToConstant: 280).isActive = true
        connectButton.target = self; connectButton.action = #selector(connectTapped)
        browserButton.target = self; browserButton.action = #selector(allowBrowsers)
        notifyButton.target = self; notifyButton.action = #selector(turnOnNotifications)
        login.target = self; login.action = #selector(loginToggled)
        done.target = self; done.action = #selector(doneTapped); done.keyEquivalent = "\r"

        let addressRow = NSStackView(views: [address, connectButton]); addressRow.spacing = 8
        let rowsView = NSStackView(views: [
            label("Vexa Capture notices when you join a Zoom or Teams call and sends Vexa's bot to it. Four things to set:", size: 12, color: .secondaryLabelColor),
            row("consent", title: "1. Tell the people on your calls", detail: "The bot is a visible participant; if it can't find your call's link, audio is captured on this Mac instead, and nobody is told. Many places need everyone's consent to transcribe a conversation.", control: consent),
            row("connect", title: "2. Connect to Vexa", detail: "Not connected", control: addressRow),
            row("browser", title: "3. Let it find your call's link", detail: "It reads your browser's open tabs for the meeting link — nothing else is kept.", control: browserButton),
            row("notify", title: "4. Notifications", detail: "How you're told a bot was sent.", control: notifyButton),
            login,
        ])
        rowsView.orientation = .vertical; rowsView.alignment = .leading; rowsView.spacing = 16
        rowsView.translatesAutoresizingMaskIntoConstraints = false
        let footer = label("Reopen this any time from the menu: Set up Vexa Capture…", size: 11, color: .tertiaryLabelColor)
        let bottom = NSStackView(views: [footer, done]); bottom.orientation = .horizontal; bottom.distribution = .fill; bottom.alignment = .centerY
        bottom.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(rowsView); v.addSubview(bottom)
        NSLayoutConstraint.activate([
            rowsView.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 24), rowsView.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -24),
            rowsView.topAnchor.constraint(equalTo: v.topAnchor, constant: 20),
            bottom.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 24), bottom.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -24),
            bottom.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -16),
        ])
    }

    // ── showing and refreshing ──
    func present() {
        address.stringValue = Settings.terminalURL
        NSApp.activate(ignoringOtherApps: true)
        window?.center()
        showWindow(nil)
        refresh(includeBrowsers: true)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in self?.refresh(includeBrowsers: false) }   // picks up a pairing done in the browser
    }

    func windowWillClose(_ notification: Notification) { timer?.invalidate(); timer = nil }

    private func mark(_ id: String, _ ok: Bool?, _ detail: String? = nil) {
        guard let r = rows[id] else { return }
        let (symbol, color): (String, NSColor) = ok == true ? ("checkmark.circle.fill", .systemGreen) : ok == false ? ("exclamationmark.circle.fill", .systemOrange) : ("circle", .tertiaryLabelColor)
        r.icon.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)?.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors: [color]))
        if let d = detail { r.detail.stringValue = d }
    }

    func refresh(includeBrowsers: Bool) {
        let previous = snapshot
        Task {
            let s = await SetupCheck.snapshot(includeBrowsers: includeBrowsers, previous: previous)
            await MainActor.run { self.apply(s) }
        }
    }

    private func apply(_ s: SetupSnapshot) {
        snapshot = s
        consent.state = s.consent ? .on : .off
        mark("consent", s.consent, nil)
        switch s.connection {
        case .ok(let who): mark("connect", true, "Connected as \(who)"); connectButton.title = "Reconnect"
        case .rejected: mark("connect", false, "Vexa doesn't accept the saved key — connect again"); connectButton.title = "Connect"
        case .unreachable: mark("connect", false, "Can't reach Vexa — check the address and your connection"); connectButton.title = "Connect"
        case .notConnected: mark("connect", false, "Not connected — enter the address you open Vexa at, then Connect. Your browser opens to confirm; nothing to copy."); connectButton.title = "Connect"
        }
        let open = s.browsers
        if open.isEmpty {
            mark("browser", nil, s.notRunning.isEmpty ? "No supported browser found." : "Open \(s.notRunning.joined(separator: ", ")), then choose Allow access — macOS will ask once per browser.")
            browserButton.isEnabled = !s.notRunning.isEmpty
        } else {
            let allowed = open.filter { $0.access == .allowed }.map { $0.name }, blocked = open.filter { $0.access != .allowed }.map { $0.name }
            mark("browser", blocked.isEmpty, (allowed.isEmpty ? "" : "Can read \(allowed.joined(separator: ", ")). ") + (blocked.isEmpty ? "" : "\(blocked.joined(separator: ", ")) needs access — choose Allow access, or turn it on in System Settings → Privacy & Security → Automation."))
            browserButton.isEnabled = true
        }
        mark("notify", s.notifications, s.notifications ? "On — you'll be told when a bot is sent or audio is captured." : "Off — turn them on so you're reminded to tell people on the call.")
        notifyButton.isHidden = s.notifications
        login.state = s.openAtLogin ? .on : .off
        let ready = s.consent && { if case .ok = s.connection { return true } else { return false } }()
        done.title = ready ? "Done" : "Later"
    }

    // ── actions ──
    @objc private func consentToggled() { Settings.consentAccepted = consent.state == .on; onChange?(); refresh(includeBrowsers: false) }
    @objc private func connectTapped() {
        guard ConnectLink.acceptableBase(address.stringValue) != nil else {
            let a = NSAlert(); a.messageText = "That isn't an https:// address (or http://localhost)."; a.beginSheetModal(for: window!); return
        }
        onConnect?(address.stringValue)
    }
    @objc private func doneTapped() { window?.close() }
    @objc private func allowBrowsers() { refresh(includeBrowsers: true) }
    @objc private func turnOnNotifications() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { [weak self] granted, _ in
            DispatchQueue.main.async {
                self?.refresh(includeBrowsers: false)
                if !granted, let url = URL(string: "x-apple.systempreferences:com.apple.Notifications-Settings.extension") { NSWorkspace.shared.open(url) }
            }
        }
    }
    @objc private func loginToggled() {
        do { if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() } else { try SMAppService.mainApp.register() } }
        catch { let a = NSAlert(); a.messageText = "Couldn't change Open at login"; a.informativeText = "\(error.localizedDescription) Try moving Vexa Capture into your Applications folder first."; a.beginSheetModal(for: window!) }
        refresh(includeBrowsers: false)
    }
}
