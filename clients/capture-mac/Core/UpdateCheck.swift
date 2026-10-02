import CryptoKit
import Foundation

/// A newer build the update feed offers.
public struct UpdateOffer: Equatable {
    public let version: String
    public let url: URL
    public let sha256: String
    public init(version: String, url: URL, sha256: String) { self.version = version; self.url = url; self.sha256 = sha256 }
}

/// The update feed is one small JSON file the release script writes next to the disk image:
/// `{"version": "0.2.0", "url": "https://…/VexaCapture-0.2.0.dmg", "sha256": "…"}`.
public enum UpdateCheck {
    public enum Result: Equatable { case upToDate, available(UpdateOffer), unavailable(String) }

    /// Dotted numbers compared part by part, so 0.10.0 is newer than 0.9.0 and a missing part counts as 0.
    public static func isNewer(_ candidate: String, than current: String) -> Bool {
        guard let a = parts(candidate), let b = parts(current) else { return false }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    private static func parts(_ v: String) -> [Int]? {
        let p = v.split(separator: ".", omittingEmptySubsequences: false).map { Int($0) }
        return p.isEmpty || p.contains(where: { $0 == nil }) ? nil : p.map { $0! }
    }

    /// The feed must name an https download and a 64-hex-digit checksum; anything else is not an offer.
    public static func parse(_ data: Data) -> UpdateOffer? {
        guard let o = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = o["version"] as? String, parts(version) != nil,
              let raw = o["url"] as? String, let url = URL(string: raw), url.scheme == "https", url.host != nil,
              let sum = (o["sha256"] as? String)?.lowercased(), sum.count == 64, sum.allSatisfy({ $0.isHexDigit })
        else { return nil }
        return UpdateOffer(version: version, url: url, sha256: sum)
    }

    public static func interpret(status: Int, body: Data, current: String) -> Result {
        guard status == 200 else { return .unavailable("the update server answered \(status)") }
        guard let offer = parse(body) else { return .unavailable("the update information isn't in the expected form") }
        return isNewer(offer.version, than: current) ? .available(offer) : .upToDate
    }

    public static func sha256Hex(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    /// A feed address is baked into the build; it must be https (or a local test server).
    public static func acceptableFeed(_ s: String) -> URL? {
        guard let u = URL(string: s.trimmingCharacters(in: .whitespaces)), let host = u.host else { return nil }
        return u.scheme == "https" || (u.scheme == "http" && ["localhost", "127.0.0.1"].contains(host)) ? u : nil
    }
}
