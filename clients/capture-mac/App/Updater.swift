import AppKit

/// Looks for a newer build at the feed address baked into this build (`VexaUpdateFeed`, set with `VEXA_UPDATE_FEED` when building),
/// and fetches it. The disk image is signed and notarized by the release script, so macOS itself vets it when opened; the checksum
/// from the feed only catches a damaged or swapped download.
enum Updater {
    static var currentVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0" }
    static var feed: URL? { (Bundle.main.object(forInfoDictionaryKey: "VexaUpdateFeed") as? String).flatMap(UpdateCheck.acceptableFeed) }

    static func check() async -> UpdateCheck.Result {
        guard let feed else { return .unavailable("this build has no update address") }
        var req = URLRequest(url: feed); req.timeoutInterval = 15; req.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            return UpdateCheck.interpret(status: (resp as? HTTPURLResponse)?.statusCode ?? 0, body: data, current: currentVersion)
        } catch { return .unavailable("couldn't reach the update server") }
    }

    /// Downloads the disk image into Downloads, checks it against the feed's checksum, and opens it so the person can drag the new app over the old.
    static func fetch(_ offer: UpdateOffer) async -> String? {
        do {
            let (tmp, resp) = try await URLSession.shared.download(from: offer.url)
            guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return "the download failed" }
            guard UpdateCheck.sha256Hex(try Data(contentsOf: tmp)) == offer.sha256 else { return "the download doesn't match its checksum, so it wasn't opened" }
            let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
            let dest = downloads.appendingPathComponent("VexaCapture-\(offer.version).dmg")
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.moveItem(at: tmp, to: dest)
            await MainActor.run { _ = NSWorkspace.shared.open(dest) }
            return nil
        } catch { return error.localizedDescription }
    }
}
