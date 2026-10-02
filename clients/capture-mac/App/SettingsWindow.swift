import AppKit

/// Server address and API key — the only two things to set up.
final class SettingsWindow: NSWindowController {
    private let gateway = NSTextField()
    private let server = NSTextField()
    private let key = NSSecureTextField()
    var onSaved: (() -> Void)?

    convenience init() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 540, height: 330), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "Vexa Capture — Settings"
        w.isReleasedWhenClosed = false
        self.init(window: w)
        build(into: w.contentView!)
    }

    private func label(_ s: String) -> NSTextField { let l = NSTextField(labelWithString: s); l.font = .systemFont(ofSize: 12, weight: .medium); return l }
    private func hint(_ s: String) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: s); l.font = .systemFont(ofSize: 11); l.textColor = .secondaryLabelColor; return l
    }

    private func build(into v: NSView) {
        gateway.placeholderString = "https://api.your-company.com"
        server.placeholderString = "wss://capture.your-company.com/ingest"
        key.placeholderString = "Your Vexa API key (a bot-scoped token)"
        let save = NSButton(title: "Save", target: self, action: #selector(saved)); save.keyEquivalent = "\r"
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelled))
        let stack = NSStackView(views: [
            label("Vexa API address"), gateway, hint("Where Vexa's bot is requested (the same address as the Vexa API)."),
            label("Audio capture address"), server, hint("Used when a call's link can't be found and audio is captured on this Mac instead."),
            label("API key"), key, hint("Mint one in Vexa → Settings → Tokens (scope: bot). It is kept in your Keychain."),
            NSStackView(views: [cancel, save]),
        ])
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 6
        (stack.views.last as? NSStackView)?.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: v.leadingAnchor, constant: 20), stack.trailingAnchor.constraint(equalTo: v.trailingAnchor, constant: -20),
            stack.topAnchor.constraint(equalTo: v.topAnchor, constant: 18),
            gateway.widthAnchor.constraint(equalTo: stack.widthAnchor), server.widthAnchor.constraint(equalTo: stack.widthAnchor), key.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
    }

    func present() {
        gateway.stringValue = Settings.gatewayURL
        server.stringValue = Settings.serverURL
        key.stringValue = Settings.apiKey ?? ""
        NSApp.activate(ignoringOtherApps: true)
        window?.center()
        showWindow(nil)
    }

    @objc private func saved() {
        guard BotRequest.normalized(gateway.stringValue) != nil else {
            let a = NSAlert(); a.messageText = "The Vexa API address isn't an http:// or https:// address."; a.runModal(); return
        }
        guard IngestURL.build(base: server.stringValue, platform: "zoom", nativeId: "x", apiKey: "x") != nil else {
            let a = NSAlert(); a.messageText = "The audio capture address isn't a ws:// or wss:// address."; a.runModal(); return
        }
        Settings.gatewayURL = gateway.stringValue
        Settings.serverURL = server.stringValue
        Settings.apiKey = key.stringValue
        window?.close()
        onSaved?()
    }
    @objc private func cancelled() { window?.close() }
}
