/**
 * L2 — the StackBridge adapter, against a fake gateway/meeting-api/redis (no stack, no network).
 * Run: npx tsx src/stack-bridge.test.ts
 */
import { createStackBridge, leaveChannel, TRANSCRIPTION_STREAM, type StackRedis } from './stack-bridge.js';
import { StackRefused } from './stack-port.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface Call { url: string; method: string; headers: Record<string, string>; body: any }

function harness(over: { botsStatus?: number; botsBody?: any; lifecycleStatus?: number } = {}) {
  const calls: Call[] = [];
  const xadds: Array<{ key: string; fields: Record<string, string> }> = [];
  const listeners = new Map<string, (m: string) => void>();
  const unsubscribed: string[] = [];
  const redis: StackRedis = {
    async xAdd(key, _id, fields) { xadds.push({ key, fields }); },
    async subscribe(channel, cb) { listeners.set(channel, cb); return async () => { unsubscribed.push(channel); listeners.delete(channel); }; },
  };
  const fetchImpl = (async (url: string, init: any) => {
    calls.push({ url, method: init?.method, headers: init?.headers ?? {}, body: init?.body ? JSON.parse(init.body) : undefined });
    if (url.endsWith('/bots')) {
      const status = over.botsStatus ?? 201;
      return new Response(JSON.stringify(over.botsBody ?? { id: 42, data: { sessions: ['sess-1'] } }), { status });
    }
    return new Response('{}', { status: over.lifecycleStatus ?? 200 });
  }) as unknown as typeof fetch;
  const bridge = createStackBridge({
    gatewayUrl: 'http://gateway:8000/', meetingApiUrl: 'http://meeting-api:8080', internalSecret: 's3cret',
    redis, fetchImpl, now: () => 1_000_000, reconnectGraceMs: 0,
  });
  return { bridge, calls, xadds, listeners, unsubscribed, redis, fetchImpl };
}

const seg = (id: string, text: string, start = 1, end = 3) => ({ segment_id: id, speaker: 'You', text, start, end, completed: true } as any);

async function main(): Promise<void> {
  // ── registering the call ──
  {
    const h = harness();
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K', language: 'en' });
    const post = h.calls[0];
    check('registers via the gateway as the caller (X-API-Key)', post.url === 'http://gateway:8000/bots' && post.headers['X-API-Key'] === 'K', JSON.stringify(post));
    check('asks for an external capture, not a bot', post.body.capture === 'external' && post.body.platform === 'zoom' && post.body.native_meeting_id === 'cap-1', JSON.stringify(post.body));
    check('subscribes to the meeting\'s leave channel', h.listeners.has(leaveChannel(42)));
    await s.end('stopped');
  }

  // ── refusals map to close codes ──
  for (const [status, code] of [[401, 4401], [403, 4401], [409, 4409], [429, 4429], [503, 4503], [500, 4503]] as const) {
    const h = harness({ botsStatus: status, botsBody: { detail: 'x' } });
    let got: unknown;
    try { await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' }); } catch (e) { got = e; }
    check(`a ${status} from the stack refuses the connection with ${code}`, got instanceof StackRefused && got.code === code, String(got));
  }
  {
    const h = harness();
    let got: unknown;
    try { await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: '' }); } catch (e) { got = e; }
    check('no API key → refused without calling the stack', got instanceof StackRefused && got.code === 4401 && h.calls.length === 0);
  }

  // ── segments reach the collector's stream ──
  {
    const h = harness();
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    s.publish([seg('a:1', 'we need CSV export', 2, 5)]);
    await sleep(5);
    const x = h.xadds[0];
    const p = JSON.parse(x.fields.payload);
    check('segments go to the transcription_segments stream', x.key === TRANSCRIPTION_STREAM);
    check('wire = {type, meeting_id, native_meeting_id, segments}', p.type === 'transcription' && p.meeting_id === 42 && p.native_meeting_id === 'cap-1' && p.platform === 'zoom' && p.segments.length === 1, JSON.stringify(p));
    check('segments are confirmed and carry absolute times', p.segments[0].completed === true && p.segments[0].absolute_start_time === new Date(1_000_000 + 2000).toISOString(), JSON.stringify(p.segments[0]));
    s.publish([]);
    await sleep(2);
    check('an empty batch publishes nothing', h.xadds.length === 1);
    await s.end('stopped');
  }

  // ── ending: stream marker, then the terminal lifecycle event ──
  {
    const h = harness();
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    await s.end('stopped');
    const marker = JSON.parse(h.xadds.at(-1)!.fields.payload);
    check('session_end marker is written', marker.type === 'session_end' && marker.meeting_id === 42, JSON.stringify(marker));
    const life = h.calls.at(-1)!;
    check('terminal lifecycle goes to meeting-api with the internal secret', life.url === 'http://meeting-api:8080/bots/internal/callback/lifecycle' && life.headers['x-internal-secret'] === 's3cret');
    check('…for the session the stack created, as completed/stopped', life.body.connection_id === 'sess-1' && life.body.status === 'completed' && life.body.completion_reason === 'stopped', JSON.stringify(life.body));
    check('unsubscribed from the leave channel', h.unsubscribed.includes(leaveChannel(42)));
    const before = h.calls.length;
    await s.end('closed');
    check('ending twice is a no-op', h.calls.length === before);
    const xaddsAfterEnd = h.xadds.length;
    s.publish([seg('a:2', 'late')]);
    await sleep(2);
    check('nothing is published after the end', h.xadds.length === xaddsAfterEnd, `${h.xadds.length} vs ${xaddsAfterEnd}`);
  }
  {
    const h = harness({ lifecycleStatus: 409 });
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    let threw = false;
    try { await s.end('stopped'); } catch { threw = true; }
    check('a 409 (already terminal) from the lifecycle callback is not an error', !threw);
  }
  {
    const h = harness({ lifecycleStatus: 500 });
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    let threw = false;
    try { await s.end('stopped'); } catch { threw = true; }
    check('any other lifecycle failure surfaces', threw);
  }

  // ── the stack asks for a stop ──
  {
    const h = harness();
    const s = await h.bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    let stopped = 0;
    s.onStop(() => stopped++);
    h.listeners.get(leaveChannel(42))!(JSON.stringify({ action: 'leave', meeting_id: 42 }));
    h.listeners.get(leaveChannel(42))!('not json');
    h.listeners.get(leaveChannel(42))!(JSON.stringify({ action: 'reconfigure' }));
    check('a leave command stops the capture (other messages ignored)', stopped === 1, String(stopped));
    await s.end('stopped');
  }

  // ── reconnect within the grace period stays one meeting ──
  {
    const calls: Call[] = [];
    const xadds: any[] = [];
    const redis: StackRedis = { async xAdd(k, _i, f) { xadds.push({ k, f }); }, async subscribe() { return async () => {}; } };
    const fetchImpl = (async (url: string, init: any) => { calls.push({ url, method: init?.method, headers: init?.headers ?? {}, body: init?.body ? JSON.parse(init.body) : undefined }); return url.endsWith('/bots') ? new Response(JSON.stringify({ id: 7, data: { sessions: ['s'] } }), { status: 201 }) : new Response('{}'); }) as unknown as typeof fetch;
    const bridge = createStackBridge({ gatewayUrl: 'http://g', meetingApiUrl: 'http://m', internalSecret: 'x', redis, fetchImpl, reconnectGraceMs: 60 });
    const a = await bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    await a.end('closed');
    const b = await bridge.open({ platform: 'zoom', native: 'cap-1', apiKey: 'K' });
    await sleep(120);
    check('a quick reconnect reuses the meeting (one POST /bots, no end reported)', calls.filter((c) => c.url.endsWith('/bots')).length === 1 && !calls.some((c) => c.url.includes('/callback/lifecycle')), JSON.stringify(calls.map((c) => c.url)));
    await b.end('closed');
    await sleep(120);
    check('…and with no reconnect the call is reported over after the grace', calls.some((c) => c.url.includes('/callback/lifecycle')) && xadds.some((x) => JSON.parse(x.f.payload).type === 'session_end'));
  }

  console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ stack-bridge (L2): registers as the caller, delivers segments, ends cleanly, honours Stop, survives a reconnect.');
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
