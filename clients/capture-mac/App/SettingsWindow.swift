import AppKit

/// Server address and API key — the only two things to set up.
final class SettingsWindow: NSWindowController {
    private let server = NSTextField()
    private let key = NSSecureTextField()
    var onSaved: (() -> Void)?

    convenience init() {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 230), styleMask: [.titled, .closable], backing: .buffered, defer: false)
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
        server.placeholderString = "wss://capture.your-company.com/ingest"
        key.placeholderString = "Your Vexa API key (a bot-scoped token)"
        let save = NSButton(title: "Save", target: self, action: #selector(saved)); save.keyEquivalent = "\r"
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelled))
        let stack = NSStackView(views: [
            label("Server"), server, hint("The capture address your Vexa deployment publishes."),
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
            server.widthAnchor.constraint(equalTo: stack.widthAnchor), key.widthAnchor.constraint(equalTo: stack.widthAnchor),
        ])
    }

    func present() {
        server.stringValue = Settings.serverURL
        key.stringValue = Settings.apiKey ?? ""
        NSApp.activate(ignoringOtherApps: true)
        window?.center()
        showWindow(nil)
    }

    @objc private func saved() {
        guard IngestURL.build(base: server.stringValue, platform: "zoom", nativeId: "x", apiKey: "x") != nil else {
            let a = NSAlert(); a.messageText = "That isn't a ws:// or wss:// address."; a.runModal(); return
        }
        Settings.serverURL = server.stringValue
        Settings.apiKey = key.stringValue
        window?.close()
        onSaved?()
    }
    @objc private func cancelled() { window?.close() }
}
