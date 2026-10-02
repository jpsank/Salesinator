import AppKit

/// What the menu-bar icon says at a glance.
enum AppState {
    case idle           // watching
    case working        // found a call, looking for its link / starting
    case bot            // Vexa's bot is on the call
    case capturing      // audio is being captured on this Mac
    case paused
    case attention      // not connected, or something to fix

    var symbol: String {
        switch self {
        case .idle: return "waveform.circle"
        case .working: return "ellipsis.circle"
        case .bot: return "checkmark.circle.fill"
        case .capturing: return "record.circle.fill"
        case .paused: return "pause.circle.fill"
        case .attention: return "exclamationmark.circle"
        }
    }
    var tint: NSColor? {
        switch self {
        case .capturing: return .systemRed
        case .bot: return .systemGreen
        case .attention: return .systemOrange
        default: return nil          // follows the menu bar's own light/dark rendering
        }
    }
    var tooltip: String {
        switch self {
        case .idle: return "Vexa Capture — watching for calls"
        case .working: return "Vexa Capture — setting up your call"
        case .bot: return "Vexa Capture — Vexa's bot is on your call"
        case .capturing: return "Vexa Capture — capturing your call's audio"
        case .paused: return "Vexa Capture — paused"
        case .attention: return "Vexa Capture — needs attention"
        }
    }

    func image() -> NSImage? {
        guard var img = NSImage(systemSymbolName: symbol, accessibilityDescription: tooltip) else { return nil }
        if let t = tint, let colored = img.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors: [t])) { img = colored; img.isTemplate = false }
        else { img.isTemplate = true }
        return img
    }
}
