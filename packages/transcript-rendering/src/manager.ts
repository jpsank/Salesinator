import type { TranscriptSegment, TranscriptState } from './types';
import { createTranscriptState, bootstrapConfirmed, applyTranscriptTick, recomputeTranscripts, retractSegments } from './state';
import { deduplicateByIdentity, deduplicateSegments, sortSegments, sortByStartTime } from './dedup';

/**
 * Raw WebSocket transcript message from the Vexa gateway.
 *
 * Format: `{ type: "transcript", speaker, confirmed: [...], pending: [...] }`
 */
export interface TranscriptMessage {
  type: 'transcript';
  meeting?: { id?: number };
  speaker?: string;
  confirmed?: TranscriptSegment[];
  pending?: TranscriptSegment[];
  ts?: string;
}

/**
 * Withdrawal of previously-delivered segments, by id.
 *
 * Format: `{ type: "transcript_retract", segment_ids: [...] }`. The producer sends it
 * alongside any pending snapshot, because a snapshot replaces only one speaker's drafts
 * and cannot reach a confirmed row.
 */
export interface TranscriptRetractMessage {
  type: 'transcript_retract';
  meeting?: { id?: number };
  segment_ids?: string[];
  ts?: string;
}

/** Anything the gateway's mutable channel delivers to the rendering pipeline. */
export type TranscriptWireMessage = TranscriptMessage | TranscriptRetractMessage;

/**
 * High-level transcript manager that encapsulates the full pipeline.
 *
 * Consumers feed it raw WS messages or REST bootstrap data and get back
 * deduplicated, sorted segments ready for rendering.
 *
 * ```ts
 * const manager = createTranscriptManager();
 *
 * // Bootstrap from REST
 * const segments = manager.bootstrap(restSegments);
 * render(segments);
 *
 * // On each WS message
 * ws.onmessage = (e) => {
 *   const segments = manager.handleMessage(JSON.parse(e.data));
 *   if (segments) render(segments);
 * };
 * ```
 */
/** Export current segments as a CSV string. Empty string if no segments available. */
exportToCSV(): string {
  const segments = this.getSegments();
  if (segments.length === 0) return "";

  // CSV Headers: text, speaker, absolute_start_time, absolute_end_time
  const header = "text,speaker,absolute_start_time,absolute_end_time\n";
  
  // Escape CSV characters (e.g., quotes inside text)
  const escapeCsv = (str: string) => `"${str.replace(/"/g, '""')}"`;

  const rows = segments.map(segment => 
    [
      escapeCsv(segment.text),
      escapeCsv(segment.speaker || ""),
      escapeCsv(segment.absolute_start_time || ""),
      escapeCsv(segment.absolute_end_time || ""),
    ].join(",")
  ).join("\n");

  return header + rows + "\n";
}

/**
 * Create a transcript manager that handles the full pipeline:
 * WS message parsing → confirmed/pending state → dedup → sort.
 */
export function createTranscriptManager<
  T extends TranscriptSegment = TranscriptSegment,
>(): TranscriptManager<T> {
  let state: TranscriptState<T> = createTranscriptState<T>();

  function finalize(segments: T[]): T[] {
    // 1. Identity dedup (by segment_id, keeps newer by updated_at)
    // 2. Sort by absolute_start_time (required input for overlap dedup)
    // 3. Overlap dedup (same speaker: adjacent duplicates, containment, expansion, tail-repeat)
    // 4. Sort by speech time for display
    return sortByStartTime(deduplicateSegments(sortSegments(deduplicateByIdentity(segments))));
  }

  return {
    bootstrap(segments: T[]): T[] {
      return finalize(bootstrapConfirmed(state, segments));
    },

    handleMessage(message: TranscriptWireMessage): T[] | null {
      if (message.type === 'transcript_retract') {
        const result = retractSegments(state, message.segment_ids || []);
        return result ? finalize(result) : null;
      }
      if (message.type !== 'transcript') return null;

      const confirmed = (message.confirmed || []) as T[];
      const pending = (message.pending || []) as T[];
      const speaker = message.speaker ?? undefined;

      const result = applyTranscriptTick(state, confirmed, pending, speaker);
      return result ? finalize(result) : null;
    },

    getSegments(): T[] {
      return finalize(recomputeTranscripts(state));
    },

    getState(): TranscriptState<T> {
      return state;
    },

    clear(): void {
      state = createTranscriptState<T>();
    },
  };
}
