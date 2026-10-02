import AVFoundation
import Foundation

/// The rep's own voice, from the default microphone, as 16 kHz mono float. Zoom's output tap (AppAudioCapture) holds
/// only the OTHER people, so this is what carries "You". Needs the Microphone permission.
final class MicCapture {
    var onSamples: (([Float], Double) -> Void)?
    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var running = false

    func start() throws {
        guard !running else { return }
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw NSError(domain: "ai.vexa.capture", code: 1, userInfo: [NSLocalizedDescriptionKey: "No microphone is available."])
        }
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: CaptureFormat.sampleRate, channels: 1, interleaved: false),
              let conv = AVAudioConverter(from: inFormat, to: outFormat) else {
            throw NSError(domain: "ai.vexa.capture", code: 2, userInfo: [NSLocalizedDescriptionKey: "Can't convert the microphone's format."])
        }
        converter = conv
        input.installTap(onBus: 0, bufferSize: 4096, format: inFormat) { [weak self] buffer, _ in
            guard let self, let conv = self.converter else { return }
            let ratio = outFormat.sampleRate / inFormat.sampleRate
            guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16) else { return }
            var consumed = false
            var error: NSError?
            conv.convert(to: out, error: &error) { _, status in
                if consumed { status.pointee = .noDataNow; return nil }
                consumed = true; status.pointee = .haveData; return buffer
            }
            guard error == nil, let ch = out.floatChannelData?[0], out.frameLength > 0 else { return }
            self.onSamples?(Array(UnsafeBufferPointer(start: ch, count: Int(out.frameLength))), Date().timeIntervalSince1970 * 1000)
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
    }
}
