/**
 * The StackBridge port — how a capture host that serves MANY people hands what it hears to the Vexa stack.
 *
 * The desktop host on its own keeps transcripts in memory for one local user. When it runs as the stack's capture
 * ingest it must instead (a) know who is connecting, (b) register the call as a real meeting in the stack, (c)
 * deliver each confirmed segment to the stack's transcript pipeline, and (d) report that the call ended. Those four
 * things are this port; ``stack-bridge.ts`` is its adapter over the stack's gateway, redis and meeting-api.
 */
import type { TranscriptSegment } from '@vexa/gmeet-pipeline';

/** Why a connection is refused — carried as the WebSocket close code so the client can tell what to do next. */
export class StackRefused extends Error {
  constructor(readonly code: 4401 | 4409 | 4429 | 4503, message: string) {
    super(message);
    this.name = 'StackRefused';
  }
}

export interface StackSessionRequest {
  platform: string;
  native: string;
  /** The caller's own Vexa API key — the meeting is created, and limited, as THAT user. */
  apiKey: string;
  language?: string;
}

export interface StackSession {
  /** Deliver confirmed segments to the stack's transcript pipeline. Fire-and-forget: a slow stack never stalls capture. */
  publish(segments: TranscriptSegment[]): void;
  /** The stack asked this capture to end (the user pressed Stop in Vexa): call ``cb`` once. */
  onStop(cb: () => void): void;
  /** The call is over. ``stopped`` = the stack asked for it; ``closed`` = the client went away. Idempotent. */
  end(reason: 'closed' | 'stopped'): Promise<void>;
}

export interface StackBridge {
  /** Authenticate the caller and register the call. Throws ``StackRefused`` to refuse the connection. */
  open(req: StackSessionRequest): Promise<StackSession>;
}
