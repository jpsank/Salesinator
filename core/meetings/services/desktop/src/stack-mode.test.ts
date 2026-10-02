/**
 * L3 — the desktop host in STACK mode: an unauthenticated or refused connection is closed with the bridge's code and
 * captures nothing; an admitted one is registered, and a Stop from the stack ends it and reports the end.
 * Run: npx tsx src/stack-mode.test.ts
 */
import { WebSocket } from 'ws';
import { startDesktop } from './desktop.js';
import { StackRefused, type StackBridge, type StackSession } from './stack-port.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function connect(port: number, query: string, headers: Record<string, string> = {}): Promise<{ ws: WebSocket; closed: Promise<{ code: number; reason: string }>; first: Promise<string> }> {
  const ws = new WebSocket(`ws://localhost:${port}/ingest?${query}`, { headers });
  const closed = new Promise<{ code: number; reason: string }>((r) => ws.on('close', (code, reason) => r({ code, reason: reason.toString() })));
  const first = new Promise<string>((r) => ws.once('message', (d) => r(d.toString())));
  return new Promise((res) => ws.on('open', () => res({ ws, closed, first }))).catch(() => ({ ws, closed, first })) as any;
}

async function main(): Promise<void> {
  // ── a refused caller is closed with the code and never captured ──
  {
    const opened: unknown[] = [];
    const bridge: StackBridge = { async open(req) { opened.push(req); throw new StackRefused(4401, 'unauthorized'); } };
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', stack: bridge });
    const { closed } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-1&api_key=BAD');
    const c = await closed;
    check('a refused connection is closed with the bridge\'s code', c.code === 4401 && c.reason === 'unauthorized', JSON.stringify(c));
    check('the bridge saw the platform, id and key', JSON.stringify(opened) === JSON.stringify([{ platform: 'zoom', native: 'cap-1', apiKey: 'BAD', language: undefined }]), JSON.stringify(opened));
    const meetings = await (await fetch(`http://localhost:${desk.gatewayPort}/bots`)).json() as any;
    check('a refused call leaves no meeting behind', meetings.meetings.length === 0, JSON.stringify(meetings));
    await desk.close();
  }

  // ── an unexpected error is a 4503, not a crash ──
  {
    const bridge: StackBridge = { async open() { throw new Error('boom'); } };
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', stack: bridge });
    const { closed } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-1&api_key=K');
    check('an unexpected bridge error closes with 4503', (await closed).code === 4503);
    await desk.close();
  }

  // ── the key may ride a header instead of the URL ──
  {
    let seen = '';
    const bridge: StackBridge = { async open(req) { seen = req.apiKey; throw new StackRefused(4409, 'busy'); } };
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', stack: bridge });
    const { closed } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-1', { 'x-api-key': 'HDR' });
    await closed;
    check('x-api-key header is accepted', seen === 'HDR', seen);
    await desk.close();
  }

  // ── an admitted caller is registered; Stop from the stack ends it ──
  {
    const events: string[] = [];
    let stop: (() => void) | null = null;
    const session: StackSession = {
      publish() { events.push('publish'); },
      onStop(cb) { stop = cb; },
      async end(reason) { events.push(`end:${reason}`); },
    };
    const bridge: StackBridge = { async open() { events.push('open'); return session; } };
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', stack: bridge });
    const { ws, closed, first } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-1&api_key=K');
    check('an admitted caller gets the ready frame', JSON.parse(await first).type === 'ready');
    check('the call was registered', events[0] === 'open');
    stop!();
    const c = await closed;
    await sleep(20);
    check('Stop from the stack closes the client socket cleanly', c.code === 1000 || c.code === 1005, String(c.code));
    check('…and reports the end as "stopped"', events.includes('end:stopped'), JSON.stringify(events));
    ws.terminate();
    await desk.close();
  }

  // ── a client that just disconnects is reported as closed ──
  {
    const events: string[] = [];
    const session: StackSession = { publish() {}, onStop() {}, async end(reason) { events.push(`end:${reason}`); } };
    const bridge: StackBridge = { async open() { return session; } };
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', stack: bridge });
    const { ws, first } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-2&api_key=K');
    await first;
    ws.close();
    await sleep(80);
    check('a client disconnect is reported as "closed"', events.join() === 'end:closed', JSON.stringify(events));
    await desk.close();
  }

  // ── without a bridge nothing changes: the local host still admits anyone ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test' });
    const { ws, first } = await connect(desk.ingestPort, 'platform=zoom&native_meeting_id=cap-3');
    check('no bridge → the unchanged local behaviour', JSON.parse(await first).type === 'ready');
    ws.terminate();
    await desk.close();
  }

  console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ stack mode (L3): refusals close with a code, admitted calls register and end cleanly, local mode unchanged.');
  process.exit(failed ? 1 : 0);
}
main().catch((e) => { console.error(e); process.exit(1); });
