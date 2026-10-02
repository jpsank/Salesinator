import Foundation
import UserNotifications

/// The "Vexa is transcribing this call" notice. A capture that is silent is a capture people can't object to, so the
/// notification is not optional UI: it is how the person is reminded to tell the others on the call.
enum Notifier {
    private static var available: Bool { Bundle.main.bundleIdentifier != nil }       // not when run as a bare binary

    static func requestPermission() {
        guard available else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    static func post(title: String, body: String) {
        guard available else { print("[notify] \(title) — \(body)"); return }
        let c = UNMutableNotificationContent()
        c.title = title; c.body = body
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: UUID().uuidString, content: c, trigger: nil))
    }
}
