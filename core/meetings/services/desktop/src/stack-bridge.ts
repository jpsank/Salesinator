/**
 * StackBridge adapter — the capture host's seam into the Vexa stack.
 *
 * The call is registered as a real meeting through the gateway, as the CALLER (their own API key), so the
 * stack's gates apply to it exactly as to a bot: STT configured, one active meeting per call, the concurrency
 * cap, the user's webhooks (which is what starts live feature-request capture). ``POST /bots`` with
 * ``capture: "external"`` creates the row and reports ``joining`` → ``active``; nothing is spawned.
 *
 * From then on this adapter plays the part a bot's own adapters play: segments go to the ``transcription_segments``
 * stream the collector drains (it persists them and feeds the copilot), a ``leave`` on the meeting's command channel
 * ends the capture, and the terminal ``lifecycle.v1`` event is posted to meeting-api's callback.
 */
import { StackRefused, type StackBridge, type StackSession } from './stack-port.js';
import type { TranscriptSegment } from '@vexa/gmeet-pipeline';

export const TRANSCRIPTION_STREAM = 'transcription_segments';
export const leaveChannel = (meetingId: number): string => `bot_commands:meeting:${meetingId}`;

export interface StackRedis {
  xAdd(key: string, id: string, fields: Record<string, string>): Promise<unknown>;
  /** Listen on a channel; resolves to an unsubscribe. */
  subscribe(channel: string, onMessage: (message: string) => void): Promise<() => Promise<void>>;
}

export interface StackBridgeOptions {
  /** The public gateway — the caller's key is checked here, and their limits and webhooks applied. */
  gatewayUrl: string;
  /** meeting-api, for the lifecycle callback only (never exposed). */
  meetingApiUrl: string;
  internalSecret: string;
  redis: StackRedis;
  fetchImpl?: typeof fetch;
  log?: (m: string) => void;
  now?: () => number;
  /** How long a dropped client has to reconnect before the call is reported over (a Wi-Fi blip must not split one call into two). */
  reconnectGraceMs?: number;
}

interface Registered { meetingId: number; sessionUid: string; startedMs: number }
interface Live extends Registered { sessions: number; endTimer?: ReturnType<typeof setTimeout> }

const trim = (u: string): string => u.replace(/\/$/, '');

export function createStackBridge(opts: StackBridgeOptions): StackBridge {
  const f = opts.fetchImpl ?? fetch;
  const log = opts.log ?? (() => { /* quiet */ });
  const now = opts.now ?? Date.now;
  const graceMs = opts.reconnectGraceMs ?? 20000;
  // A client that drops and reconnects mid-call must land back on the SAME meeting, not 409 against itself.
  const live = new Map<string, Live>();
  const identity = (apiKey: string, platform: string, native: string): string => `${apiKey}|${platform}/${native}`;

  async function register(apiKey: string, platform: string, native: string, language?: string): Promise<Registered> {
    const res = await f(`${trim(opts.gatewayUrl)}/bots`, {
      method: 'POST',
      headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ platform, native_meeting_id: native, capture: 'external', ...(language ? { language } : {}) }),
    });
    if (res.status === 401) throw new StackRefused(4401, 'unauthorized — Vexa did not accept this API key');
    if (res.status === 403) throw new StackRefused(4401, 'forbidden — this key may not start a capture or the service refused it');
    if (res.status === 409) throw new StackRefused(4409, 'a capture or bot is already active for this call');
    if (res.status === 429) throw new StackRefused(4429, 'concurrent meeting limit reached');
    if (res.status === 503) throw new StackRefused(4503, `the stack cannot take this call: ${(await res.text()).slice(0, 160)}`);
    if (res.status !== 201) throw new StackRefused(4503, `unexpected ${res.status} from the stack while starting the capture`);
    const body = await res.json() as { id: number; data?: { sessions?: string[] } };
    const sessionUid = body.data?.sessions?.slice(-1)[0];
    if (!body.id || !sessionUid) throw new StackRefused(4503, 'the stack answered without a meeting id and session');
    return { meetingId: body.id, sessionUid, startedMs: now() };
  }

  async function postLifecycle(reg: Registered, reason: 'closed' | 'stopped'): Promise<void> {
    // Both ends are a stop as far as the stack is concerned: the capture ended because someone asked or left.
    const res = await f(`${trim(opts.meetingApiUrl)}/bots/internal/callback/lifecycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-internal-secret': opts.internalSecret },
      body: JSON.stringify({
        connection_id: reg.sessionUid, status: 'completed', completion_reason: 'stopped',
        reason: reason === 'stopped' ? 'stopped from Vexa' : 'the capture client disconnected',
        timestamp: new Date(now()).toISOString(),
      }),
    });
    if (!res.ok && res.status !== 409) throw new Error(`lifecycle callback answered ${res.status}`);   // 409 = already terminal
  }

  async function finalize(id: string, reg: Live, native: string, platform: string, reason: 'closed' | 'stopped'): Promise<void> {
    if (live.get(id) !== reg || reg.sessions > 0) return;        // reconnected in the meantime
    live.delete(id);
    // The collector turns this marker into the copilot's end-of-meeting; it must precede the terminal status.
    await opts.redis.xAdd(TRANSCRIPTION_STREAM, '*', {
      payload: JSON.stringify({ type: 'session_end', meeting_id: reg.meetingId, native_meeting_id: native, platform }),
    });
    await postLifecycle(reg, reason);
    log(`[stack] ■ meeting ${reg.meetingId} (${reason})`);
  }

  return {
    async open({ platform, native, apiKey, language }): Promise<StackSession> {
      if (!apiKey) throw new StackRefused(4401, 'no API key — pass api_key on the ingest URL');
      const id = identity(apiKey, platform, native);
      let reg = live.get(id);
      if (reg) {
        reg.sessions++;
        if (reg.endTimer) { clearTimeout(reg.endTimer); reg.endTimer = undefined; log(`[stack] ↺ meeting ${reg.meetingId} reconnected`); }
      } else {
        const created = await register(apiKey, platform, native, language);
        reg = { ...created, sessions: 1 };
        live.set(id, reg);
        log(`[stack] ▶ meeting ${reg.meetingId} (${platform}/${native})`);
      }
      const registered = reg;
      let stopCb: (() => void) | null = null;
      let ended = false;
      let unsubscribe: (() => Promise<void>) | null = null;
      opts.redis.subscribe(leaveChannel(registered.meetingId), (message) => {
        let action: unknown;
        try { action = JSON.parse(message)?.action; } catch { return; }
        if (action === 'leave') stopCb?.();
      }).then((u) => { unsubscribe = u; if (ended) void u(); }).catch((e) => log(`[stack] leave subscription FAILED for ${registered.meetingId}: ${e?.message || e}`));

      return {
        publish(segments: TranscriptSegment[]): void {
          if (ended || segments.length === 0) return;
          const wire = segments.map((s) => ({
            ...s,
            completed: true,
            absolute_start_time: new Date(registered.startedMs + s.start * 1000).toISOString(),
            absolute_end_time: new Date(registered.startedMs + s.end * 1000).toISOString(),
          }));
          const payload = JSON.stringify({ type: 'transcription', meeting_id: registered.meetingId, native_meeting_id: native, platform, segments: wire });
          opts.redis.xAdd(TRANSCRIPTION_STREAM, '*', { payload }).catch((e) => log(`[stack] publish FAILED for ${registered.meetingId}: ${e?.message || e}`));
        },
        onStop(cb: () => void): void { stopCb = cb; },
        async end(reason): Promise<void> {
          if (ended) return;
          ended = true;
          if (unsubscribe) await unsubscribe().catch(() => { /* best-effort */ });
          registered.sessions--;
          if (registered.sessions > 0) return;                     // a newer connection for this call took over
          if (reason === 'closed' && graceMs > 0) {
            // The client went away; give it a moment to come back before telling the stack the call is over.
            registered.endTimer = setTimeout(() => { void finalize(id, registered, native, platform, reason).catch((e) => log(`[stack] end FAILED for ${registered.meetingId}: ${e?.message || e}`)); }, graceMs);
            registered.endTimer.unref?.();
            return;
          }
          await finalize(id, registered, native, platform, reason);
        },
      };
    },
  };
}
