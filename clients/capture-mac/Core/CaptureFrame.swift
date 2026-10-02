import Foundation

/// The capture.v1 wire (core/meetings/modules/capture-codec): what a capture client sends the ingest.
/// An audio frame is little-endian — Int32 channel, Float64 capture time (ms since the epoch, stamped before the
/// network), then 32-bit float PCM at 16 kHz mono. The mixed lane the ingest uses for Zoom and Teams takes everyone
/// else on the call on channel 999 and the local microphone on channel 1000 (always labelled "You").
public enum CaptureChannel {
    public static let remote: Int32 = 999
    public static let mic: Int32 = 1000
}

public enum CaptureFormat {
    public static let sampleRate: Double = 16000
    /// 100 ms — what the browser extension sends, and small enough that a stalled network drops little.
    public static let frameSamples = 1600
}

public func encodeAudioFrame(channel: Int32, timestampMs: Double, samples: [Float]) -> Data {
    var data = Data(capacity: 12 + samples.count * 4)
    var ch = channel.littleEndian
    var ts = timestampMs.bitPattern.littleEndian
    withUnsafeBytes(of: &ch) { data.append(contentsOf: $0) }
    withUnsafeBytes(of: &ts) { data.append(contentsOf: $0) }
    samples.withUnsafeBufferPointer { buf in
        for s in buf {
            var bits = s.bitPattern.littleEndian
            withUnsafeBytes(of: &bits) { data.append(contentsOf: $0) }
        }
    }
    return data
}

/// Collects PCM of any chunk size and hands back fixed 100 ms frames, remembering the capture time of each frame's
/// first sample (the clock is read when the audio ARRIVES, not when a frame fills).
public final class FrameChunker {
    private var pending: [Float] = []
    private var pendingStartMs: Double = 0
    private let frameSamples: Int
    public init(frameSamples: Int = CaptureFormat.frameSamples) { self.frameSamples = frameSamples }

    public func append(_ samples: [Float], arrivedAtMs: Double) -> [(timestampMs: Double, samples: [Float])] {
        if pending.isEmpty { pendingStartMs = arrivedAtMs - Double(samples.count) / CaptureFormat.sampleRate * 1000 }
        pending.append(contentsOf: samples)
        var out: [(Double, [Float])] = []
        while pending.count >= frameSamples {
            out.append((pendingStartMs, Array(pending.prefix(frameSamples))))
            pending.removeFirst(frameSamples)
            pendingStartMs += Double(frameSamples) / CaptureFormat.sampleRate * 1000
        }
        return out
    }
    public func reset() { pending.removeAll() }
}
