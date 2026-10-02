import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

/// Everything ONE app plays — the other people on the call — read through ScreenCaptureKit, which can tap a single
/// application's output without a driver and without the app's cooperation. It needs the Screen Recording permission
/// (macOS gates app audio behind it); no pixels are kept: the video side is configured to the smallest, slowest frame
/// and discarded.
final class AppAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    enum CaptureError: LocalizedError {
        case appNotFound(String)
        var errorDescription: String? {
            if case .appNotFound(let n) = self { return "\(n) isn't running." }
            return nil
        }
    }

    /// 16 kHz mono float samples, with the time (ms since the epoch) they arrived.
    var onSamples: (([Float], Double) -> Void)?
    var onFailure: ((Error) -> Void)?
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "ai.vexa.capture.app-audio")

    func start(bundleIDs: [String], displayName: String) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        let apps = content.applications.filter { bundleIDs.contains($0.bundleIdentifier) }
        guard !apps.isEmpty, let display = content.displays.first else { throw CaptureError.appNotFound(displayName) }
        let filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.sampleRate = Int(CaptureFormat.sampleRate)
        cfg.channelCount = 1
        cfg.excludesCurrentProcessAudio = true
        cfg.width = 2; cfg.height = 2                               // audio is all we want
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)   // never read, but the stream expects a sink
        try await s.startCapture()
        stream = s
    }

    func stop() async {
        guard let s = stream else { return }
        stream = nil
        try? await s.stopCapture()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid, let samples = Self.floats(from: sb), !samples.isEmpty else { return }
        onSamples?(samples, Date().timeIntervalSince1970 * 1000)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) { onFailure?(error) }

    /// Float32 PCM out of a sample buffer, mixed down to mono if ScreenCaptureKit hands back more than one channel.
    private static func floats(from sb: CMSampleBuffer) -> [Float]? {
        var result: [Float]?
        try? sb.withAudioBufferList { list, _ in
            let buffers = Array(list)
            guard !buffers.isEmpty else { return }
            let counts = buffers.map { Int($0.mDataByteSize) / MemoryLayout<Float>.size }
            if buffers.count == 1, buffers[0].mNumberChannels > 1 {            // interleaved
                let ch = Int(buffers[0].mNumberChannels)
                guard let d = buffers[0].mData?.assumingMemoryBound(to: Float.self) else { return }
                let frames = counts[0] / ch
                result = (0..<frames).map { f in (0..<ch).reduce(Float(0)) { $0 + d[f * ch + $1] } / Float(ch) }
                return
            }
            let n = counts.min() ?? 0                                          // planar: average the channels
            var mixed = [Float](repeating: 0, count: n)
            for b in buffers {
                guard let d = b.mData?.assumingMemoryBound(to: Float.self) else { continue }
                for i in 0..<n { mixed[i] += d[i] / Float(buffers.count) }
            }
            result = mixed
        }
        return result
    }
}
