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

    /// Takes the bot off the call. A bot that is already gone counts as removed.
    static func stop(platform: String, nativeId: String, gateway: String, key: String) async -> BotRequest.Removal {
        guard let req = BotRequest.stop(gateway: gateway, key: key, platform: platform, nativeId: nativeId) else { return .failed("the Vexa API address in Settings isn't valid") }
        do {
            let (_, resp) = try await URLSession.shared.data(for: req)
            return BotRequest.interpretStop(status: (resp as? HTTPURLResponse)?.statusCode ?? 0)
        } catch { return .failed("can't reach Vexa") }
    }

    /// Which bots Vexa still has running for this key.
    static func running(gateway: String, key: String) async -> BotRequest.Presence {
        guard let req = BotRequest.running(gateway: gateway, key: key) else { return .unknown }
        guard let (data, resp) = try? await URLSession.shared.data(for: req) else { return .unknown }
        return BotRequest.interpretRunning(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data)
    }
}
