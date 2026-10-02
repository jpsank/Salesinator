import AppKit
import AVFoundation
import CoreGraphics
import ServiceManagement
import UserNotifications

/// "Is everything set up?" — run once after pairing and from the menu, so permission prompts appear when the person is looking
/// at the app rather than in the middle of a call.
enum SetupCheck {
    /// Blocking in parts (osascript, the network): call off the main thread.
    static func run() async -> [SetupItem] {
        var items: [SetupItem] = []

        // Vexa accepts the key.
        if let key = Settings.apiKey, !key.isEmpty, let req = BotRequest.me(gateway: Settings.gatewayURL, key: key) {
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                switch BotRequest.interpretMe(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data) {
                case .account(let who): items.append(SetupItem("Connected to Vexa", .ok, "as \(who)"))
                case .keyRejected: items.append(SetupItem("Connected to Vexa", .attention, "Vexa doesn't accept this key — choose Reconnect to Vexa"))
                case .unavailable(let why): items.append(SetupItem("Connected to Vexa", .attention, "\(why) — is the server up?"))
                }
            } catch { items.append(SetupItem("Connected to Vexa", .attention, "can't reach \(Settings.gatewayURL)")) }
        } else {
            items.append(SetupItem("Connected to Vexa", .attention, "not connected — choose Connect to Vexa"))
        }

        // The browsers can be read (this is what triggers macOS's "control Chrome?" prompt).
        let browsers = await Task.detached { BrowserLinks.access() }.value
        for b in browsers.running {
            switch b.access {
            case .allowed: items.append(SetupItem(b.name, .ok, "tabs can be read to find a call's link"))
            case .denied: items.append(SetupItem(b.name, .attention, "allow Vexa Capture under Privacy & Security → Automation"))
            case .unknown: items.append(SetupItem(b.name, .info, "didn't answer — try again"))
            }
        }
        if browsers.running.isEmpty { items.append(SetupItem("Browsers", .info, browsers.notRunning.isEmpty ? "none found" : "open \(browsers.notRunning.joined(separator: ", ")) and run this check to allow it")) }

        // Notifications.
        let center = UNUserNotificationCenter.current()
        var status = await center.notificationSettings().authorizationStatus
        if status == .notDetermined { _ = try? await center.requestAuthorization(options: [.alert, .sound]); status = await center.notificationSettings().authorizationStatus }
        items.append(status == .authorized || status == .provisional
            ? SetupItem("Notifications", .ok, "you'll be told when a bot is sent or audio is captured")
            : SetupItem("Notifications", .attention, "turn them on in System Settings → Notifications — they are how you're reminded to tell people on the call"))

        // Start at login.
        items.append(SMAppService.mainApp.status == .enabled
            ? SetupItem("Open at login", .ok, "on")
            : SetupItem("Open at login", .info, "off — choose Open at login in the menu so it is running when a call starts"))

        // Hearing a call on this Mac: the fallback when a call's link can't be found or the bot can't get in.
        let audio = AudioAccess.describe(microphone: AVCaptureDevice.authorizationStatus(for: .audio) == .authorized, screen: CGPreflightScreenCaptureAccess())
        items.append(SetupItem("Hearing calls on this Mac", audio.ok ? .ok : .attention, audio.detail))
        return items
    }
}


/// The same facts as `SetupCheck.run()`, kept structured so the setup window can show each as its own row.
struct SetupSnapshot {
    enum Connection: Equatable { case ok(String), rejected, unreachable, notConnected }
    var consent: Bool
    var connection: Connection
    var browsers: [BrowserLinks.BrowserAccess]
    var notRunning: [String]
    var notifications: Bool
    var openAtLogin: Bool
    var microphone: Bool
    var screenAudio: Bool
}

extension SetupCheck {
    /// ``includeBrowsers`` asks each open browser for Automation access — which is what shows macOS's prompt — so it is only
    /// done on request (the first look, and the Allow button), not on every refresh.
    static func snapshot(includeBrowsers: Bool, previous: SetupSnapshot? = nil) async -> SetupSnapshot {
        var connection = SetupSnapshot.Connection.notConnected
        if let key = Settings.apiKey, !key.isEmpty, let req = BotRequest.me(gateway: Settings.gatewayURL, key: key) {
            do {
                let (data, resp) = try await URLSession.shared.data(for: req)
                switch BotRequest.interpretMe(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data) {
                case .account(let who): connection = .ok(Settings.account.isEmpty ? who : Settings.account)
                case .keyRejected: connection = .rejected
                case .unavailable: connection = .unreachable
                }
            } catch { connection = .unreachable }
        }
        var browsers = previous?.browsers ?? [], notRunning = previous?.notRunning ?? []
        if includeBrowsers { let r = await Task.detached { BrowserLinks.access() }.value; browsers = r.running; notRunning = r.notRunning }
        let status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        return SetupSnapshot(consent: Settings.consentAccepted, connection: connection, browsers: browsers, notRunning: notRunning,
                             notifications: status == .authorized || status == .provisional, openAtLogin: SMAppService.mainApp.status == .enabled,
                             microphone: AVCaptureDevice.authorizationStatus(for: .audio) == .authorized, screenAudio: CGPreflightScreenCaptureAccess())
    }
}
