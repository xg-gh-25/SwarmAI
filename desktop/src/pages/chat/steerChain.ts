/**
 * steerChain — how the segments of a steered turn are shown
 * (docs/design/2026-10-25-chat-steer-frontend.md §7).
 *
 * A turn the CLI read a steer in is several assistant bubbles with the read
 * steer's user bubble between them, live and in the reloaded history alike (the
 * DB rows have that shape). Without this, the segment before a steer would look
 * like the turn's end two seconds after it stops streaming (RESULT, output card,
 * next steps), and every segment would draw its own header. Pure over the
 * message list, so live == reload: it reads only roles and `metadata.steered`.
 *
 * Per assistant bubble (only the ones with a flag are in the map):
 * - `continued` -- the next non-system message is a read steer;
 * - `afterSteer` -- the previous non-system message is a read steer;
 * - `headerLive` -- the chain's first segment (the one with the header) while
 *   the chain's last segment is ``streamingId``.
 * Only a read steer splits a turn: a Waiting or Queued bubble is not one (and
 * never sits between a steer and its segment: the segment opens right after the
 * steer, later sends are appended after it).
 *
 * @exports steerChain, SteerFlags
 */
import type { Message } from '../../types';

export interface SteerFlags {
  continued: boolean;
  afterSteer: boolean;
  headerLive: boolean;
}

const isReadSteer = (m: Message | undefined) => !!m && m.role === 'user' && !!m.metadata?.steered;

export function steerChain(messages: readonly Message[], streamingId?: string): Map<string, SteerFlags> {
  const ms = messages.filter((m) => m.role !== 'system');
  const out = new Map<string, SteerFlags>();
  let chainHead: string | null = null;
  for (let i = 0; i < ms.length; i += 1) {
    const m = ms[i];
    if (m.role !== 'assistant') continue;
    const continued = isReadSteer(ms[i + 1]);
    // a segment with no earlier one shown (the history page starts at the steer)
    // keeps its header
    const afterSteer = isReadSteer(ms[i - 1]) && chainHead !== null;
    if (!afterSteer) chainHead = m.id;
    if (continued || afterSteer) out.set(m.id, { continued, afterSteer, headerLive: false });
    // the chain ends here: the pulse belongs to its head while this one streams
    if (!continued && afterSteer && m.id === streamingId && chainHead && out.has(chainHead)) {
      out.get(chainHead)!.headerLive = true;
    }
  }
  return out;
}
