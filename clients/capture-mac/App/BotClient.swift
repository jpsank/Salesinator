import Foundation

/// Sends Vexa's bot to a call, and takes it off again — the same API a rep's "add bot from URL" uses.
enum BotClient {
    static func send(to link: MeetingLink, gateway: String, key: String) async -> BotRequest.Outcome {
        guard let req = BotRequest.create(gateway: gateway, key: key, meetingURL: link.url) else {
            return .refused("the Vexa API address in Settings isn't a valid http:// or https:// URL")
        }
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            return BotRequest.interpret(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data)
        } catch {
            return .unavailable("can't reach \(gateway): \(error.localizedDescription)")
        }
    }

    /// Best-effort: the bot leaves a finished meeting by itself, so a failure here is not worth telling anyone.
    static func stop(platform: String, nativeId: String, gateway: String, key: String) async {
        guard let req = BotRequest.stop(gateway: gateway, key: key, platform: platform, nativeId: nativeId) else { return }
        _ = try? await URLSession.shared.data(for: req)
    }
}
