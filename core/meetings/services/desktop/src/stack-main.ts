/**
 * The capture ingest — the desktop host run as a stack service.
 *
 * Clients (the Mac capture app, the browser extension) stream ``capture.v1`` audio to ``ws://…/ingest`` with the
 * user's Vexa API key; each call becomes a meeting in the stack, transcribed by the stack's own STT and fed to the
 * same pipeline a bot feeds. Configuration is all environment:
 *
 *   TRANSCRIPTION_SERVICE_URL / _TOKEN   the stack's speech-to-text (required, as for a bot)
 *   CAPTURE_GATEWAY_URL                  the stack's public gateway           (default http://gateway:8000)
 *   CAPTURE_MEETING_API_URL              meeting-api, for the lifecycle callback (default http://meeting-api:8080)
 *   INTERNAL_API_SECRET                  shared with meeting-api
 *   REDIS_URL                            the transcript stream's redis        (default redis://redis:6379/0)
 *   CAPTURE_INGEST_PORT                  default 9099
 *   CAPTURE_MAX_SESSIONS                 simultaneous calls this host takes   (default 20)
 */
import { createClient } from 'redis';
import { startDesktop, type Desktop } from './desktop.js';
import { createStackBridge, type StackRedis } from './stack-bridge.js';

export function redisFrom(url: string): StackRedis {
  const pub = createClient({ url });
  const sub = createClient({ url });
  pub.on('error', (e) => console.error('[capture] redis (publish):', e.message));
  sub.on('error', (e) => console.error('[capture] redis (subscribe):', e.message));
  const ready = Promise.all([pub.connect(), sub.connect()]);
  return {
    async xAdd(key, id, fields) { await ready; return pub.xAdd(key, id, fields); },
    async subscribe(channel, onMessage) {
      await ready;
      await sub.subscribe(channel, (message) => onMessage(message));
      return async () => { await sub.unsubscribe(channel); };
    },
  };
}

export async function startStackCapture(env: NodeJS.ProcessEnv = process.env): Promise<Desktop> {
  const need = (k: string): string => {
    const v = env[k];
    if (!v) throw new Error(`${k} is required for the capture ingest`);
    return v;
  };
  const bridge = createStackBridge({
    gatewayUrl: env.CAPTURE_GATEWAY_URL || 'http://gateway:8000',
    meetingApiUrl: env.CAPTURE_MEETING_API_URL || 'http://meeting-api:8080',
    internalSecret: need('INTERNAL_API_SECRET'),
    redis: redisFrom(env.REDIS_URL || 'redis://redis:6379/0'),
    log: (m) => console.log(m),
  });
  need('TRANSCRIPTION_SERVICE_URL');
  return startDesktop({
    stack: bridge,
    ingestPort: Number(env.CAPTURE_INGEST_PORT) || 9099,
    gatewayHost: '127.0.0.1',
    maxSessions: Number(env.CAPTURE_MAX_SESSIONS) || 20,
  });
}

if (import.meta.url === `file://${process.argv[1]}`) {
  startStackCapture().catch((e) => { console.error(e); process.exit(1); });
}
