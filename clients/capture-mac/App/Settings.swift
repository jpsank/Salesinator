import Foundation
import Security

/// What the person has chosen. The API key lives in the Keychain, never in preferences or logs.
enum Settings {
    private static let d = UserDefaults.standard

    static var serverURL: String {
        get { d.string(forKey: "serverURL") ?? "ws://localhost:19099/ingest" }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "serverURL") }
    }
    /// The Vexa API (the gateway) — where the bot is requested. The ingest above is only for audio captured on this Mac.
    static var gatewayURL: String {
        get { d.string(forKey: "gatewayURL") ?? "http://localhost:18056" }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "gatewayURL") }
    }
    /// What happens when a call starts. `bot`: find the call's link and send Vexa's bot (falling back to capturing audio
    /// here when there is no link); `audio`: always capture audio on this Mac.
    enum Mode: String { case bot, audio }
    static var mode: Mode {
        get { Mode(rawValue: d.string(forKey: "mode") ?? "") ?? .bot }
        set { d.set(newValue.rawValue, forKey: "mode") }
    }
    /// Capture a call the moment it is detected. Off: it is only offered, and starts when the person says so.
    static var autoCapture: Bool {
        get { d.object(forKey: "autoCapture") as? Bool ?? true }
        set { d.set(newValue, forKey: "autoCapture") }
    }
    static func isEnabled(_ p: CallPlatform) -> Bool { d.object(forKey: "enabled.\(p.rawValue)") as? Bool ?? true }
    static func setEnabled(_ p: CallPlatform, _ on: Bool) { d.set(on, forKey: "enabled.\(p.rawValue)") }
    /// The first-run acknowledgement that the person will tell people on their calls they are being transcribed.
    static var consentAccepted: Bool {
        get { d.bool(forKey: "consentAccepted") }
        set { d.set(newValue, forKey: "consentAccepted") }
    }

    private static let service = "ai.vexa.capture", account = "apiKey"

    static var apiKey: String? {
        get {
            let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
            var out: CFTypeRef?
            guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
            return String(data: data, encoding: .utf8)
        }
        set {
            let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
            SecItemDelete(base as CFDictionary)
            guard let v = newValue?.trimmingCharacters(in: .whitespacesAndNewlines), !v.isEmpty else { return }
            var add = base; add[kSecValueData as String] = Data(v.utf8)
            SecItemAdd(add as CFDictionary, nil)
        }
    }
}
