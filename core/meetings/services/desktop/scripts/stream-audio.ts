/**
 * stream-audio — play a recording into a capture ingest as if a client were capturing it live.
 *
 *   VEXA_API_KEY=<a bot-scoped key> npx tsx scripts/stream-audio.ts call.wav \
 *     [--url ws://localhost:19099/ingest] [--platform zoom] [--id cap-<random>] [--mic mic.wav]
 *
 * ``call.wav`` is the remote side (everyone on the call but you, the mixed lane's channel 999); ``--mic`` is your own
 * voice (channel 1000, always labelled "You"). Both are 16-bit mono PCM WAV at 16 kHz — e.g. on macOS
 * ``say -o call.wav --data-format=LEI16@16000 "…"``. Frames are paced in real time, so a 30 s file takes 30 s.
 * Use it to check a deployment's capture ingest end to end (the call should appear in Meetings with a transcript).
 */
import { readFileSync } from 'node:fs';
import { WebSocket } from 'ws';
import { encodeAudioFrame } from '@vexa/capture-codec';

const SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 1600;                    // 100 ms
const MIXED_CHANNEL = 999, MIC_CHANNEL = 1000;

export function wavToFloat32(buf: Buffer): Float32Array {
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') throw new Error('not a WAV file');
  let offset = 12, fmt: { channels: number; rate: number; bits: number } | null = null;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === 'fmt ') fmt = { channels: buf.readUInt16LE(offset + 10), rate: buf.readUInt32LE(offset + 12), bits: buf.readUInt16LE(offset + 22) };
    if (id === 'data') {
      if (!fmt || fmt.channels !== 1 || fmt.rate !== SAMPLE_RATE || fmt.bits !== 16) throw new Error(`need 16-bit mono ${SAMPLE_RATE} Hz PCM, got ${JSON.stringify(fmt)}`);
      const n = Math.floor(Math.min(size, buf.length - offset - 8) / 2);
      const out = new Float32Array(n);
      for (let i = 0; i < n; i++) out[i] = buf.readInt16LE(offset + 8 + i * 2) / 32768;
      return out;
    }
    offset += 8 + size + (size & 1);
  }
  throw new Error('no data chunk');
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const flag = (name: string, fallback?: string): string | undefined => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : fallback; };
  const file = args.find((a) => a.endsWith('.wav') && args[args.indexOf(a) - 1] !== '--mic');
  const key = process.env.VEXA_API_KEY;
  if (!file || !key) { console.error('usage: VEXA_API_KEY=… stream-audio.ts call.wav [--mic mic.wav] [--url …] [--platform zoom] [--id …]'); process.exit(2); }
  const remote = wavToFloat32(readFileSync(file));
  const micFile = flag('mic');
  const mic = micFile ? wavToFloat32(readFileSync(micFile)) : null;
  const platform = flag('platform', 'zoom')!;
  const id = flag('id', `cap-${Math.random().toString(36).slice(2, 10)}`)!;
  const url = `${flag('url', 'ws://localhost:19099/ingest')}?platform=${platform}&native_meeting_id=${id}&api_key=${encodeURIComponent(key)}`;

  const ws = new WebSocket(url);
  const closed = new Promise<{ code: number; reason: string }>((r) => ws.on('close', (code, reason) => r({ code, reason: reason.toString() })));
  await new Promise<void>((res, rej) => { ws.once('message', () => res()); ws.once('error', rej); closed.then((c) => rej(new Error(`refused: ${c.code} ${c.reason}`))); });
  console.log(`connected — streaming ${(remote.length / SAMPLE_RATE).toFixed(1)} s as ${platform}/${id}`);

  const total = Math.max(remote.length, mic?.length ?? 0);
  for (let at = 0; at < total && ws.readyState === WebSocket.OPEN; at += FRAME_SAMPLES) {
    const ts = Date.now();
    if (at < remote.length) ws.send(encodeAudioFrame(MIXED_CHANNEL, ts, remote.subarray(at, at + FRAME_SAMPLES)));
    if (mic && at < mic.length) ws.send(encodeAudioFrame(MIC_CHANNEL, ts, mic.subarray(at, at + FRAME_SAMPLES)));
    await new Promise((r) => setTimeout(r, 100));
  }
  // Let the pipeline flush its last segment before hanging up.
  await new Promise((r) => setTimeout(r, 6000));
  ws.close();
  console.log(`done — look for ${platform}/${id} in Meetings (the call ends when this client disconnects)`);
  process.exit(0);
}
if (import.meta.url === `file://${process.argv[1]}`) main().catch((e) => { console.error(String(e?.message || e)); process.exit(1); });
