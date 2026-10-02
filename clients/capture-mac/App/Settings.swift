import Foundation
import Security

/// What the person has chosen. The API key lives in the Keychain, never in preferences or logs.
enum Settings {
    private static let d = UserDefaults.standard

    static var serverURL: String {
        get { d.string(forKey: "serverURL") ?? "ws://localhost:19099/ingest" }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "serverURL") }
    }
    /// The Vexa web address the app pairs with (where the person is signed in) and who they connected as.
    /// A build can bake in the address people open Vexa at (`VEXA_ADDRESS=https://… ./install.sh`); otherwise it is this Mac's.
    private static var defaultAddress: String {
        (Bundle.main.object(forInfoDictionaryKey: "VexaDefaultAddress") as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "http://localhost:13000"
    }
    static var terminalURL: String {
        get { d.string(forKey: "terminalURL") ?? defaultAddress }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "terminalURL") }
    }
    static var account: String {
        get { d.string(forKey: "account") ?? "" }
        set { d.set(newValue, forKey: "account") }
    }
    /// Sites the person has confirmed (typed the address, or approved a pairing); a build made for an address trusts it from the start.
    static var trustedBases: [String] {
        get {
            var all = d.stringArray(forKey: "trustedBases") ?? []
            if let baked = Bundle.main.object(forInfoDictionaryKey: "VexaDefaultAddress") as? String, !baked.isEmpty { all.append(baked) }
            return all
        }
        set { d.set(newValue, forKey: "trustedBases") }
    }
    static func trust(_ base: URL) { trustedBases = TrustedBases.adding(d.stringArray(forKey: "trustedBases") ?? [], base) }
    static var lastUpdateCheck: Date? {
        get { d.object(forKey: "lastUpdateCheck") as? Date }
        set { d.set(newValue, forKey: "lastUpdateCheck") }
    }
    static var callLog: [CallRecord] {
        get { (d.data(forKey: "callLog").flatMap { try? JSONDecoder().decode([CallRecord].self, from: $0) }) ?? [] }
        set { d.set(try? JSONEncoder().encode(newValue), forKey: "callLog") }
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

    private static let service = "ai.vexa.capture", keychainAccount = "apiKey"

    /// The Keychain is asked once per run, not per use: an ad-hoc-signed build has no stable identity, so macOS asks for the login
    /// password on every Keychain read until the person chooses Always Allow — and the menu and setup window check the key constantly.
    /// What was read (or that nothing could be) is kept for the run; saving a key replaces it.
    private static let keyLock = NSLock()
    private static var keyLoaded = false
    private static var keyValue: String?

    static var apiKey: String? {
        get {
            keyLock.lock(); defer { keyLock.unlock() }
            if !keyLoaded { keyValue = readKeychain(); keyLoaded = true }
            return keyValue
        }
        set {
            keyLock.lock(); defer { keyLock.unlock() }
            let v = newValue?.trimmingCharacters(in: .whitespacesAndNewlines)
            writeKeychain(v)
            keyValue = (v?.isEmpty ?? true) ? nil : v
            keyLoaded = true
        }
    }

    private static func readKeychain() -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: keychainAccount, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func writeKeychain(_ value: String?) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: keychainAccount]
        SecItemDelete(base as CFDictionary)
        guard let v = value, !v.isEmpty else { return }
        var add = base; add[kSecValueData as String] = Data(v.utf8)
        SecItemAdd(add as CFDictionary, nil)
    }
}
