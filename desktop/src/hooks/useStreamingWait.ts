/**
 * useStreamingWait — what a long-quiet streaming turn is waiting on, as a fact.
 *
 * Returns null until the message content has not changed for WAIT_HINT_MS (45s);
 * then, ticking once a second, the first that holds:
 *   1. a call has no result yet        → { kind: 'tool', tool, object, since }
 *   2. background Agents still run     → { kind: 'agents', count, since }
 *   3. thinking_progress is arriving   → { kind: 'thinking', since }
 *   4. otherwise                       → { kind: 'model', since }
 * `since` is when that wait began, so the hint shows its real length. A content
 * change hides the hint at once and restarts the 45s. It never guesses a cause:
 * real problems (a stall, a lost connection, a paused backend) are reported by the
 * still_working notice, the reconnect banner and the health monitor.
 *
 * The bee keeps flying the whole time: this is a hint, not a state change.
 *
 * @exports useStreamingWait, formatWait, WAIT_HINT_MS, StreamingWait
 */
import { useEffect, useRef, useState } from 'react';
import type { ContentBlock, ToolUseContent } from '../types';
import { useBackgroundAgents } from '../stores/backgroundAgentsStore';
import { thinkingSince } from '../stores/thinkingProgressStore';
import { stripView } from '../pages/chat/turnZones';

export const WAIT_HINT_MS = 45_000;

export type StreamingWait =
  | { kind: 'tool'; tool: string; object: string; since: number }
  | { kind: 'agents'; count: number; since: number }
  | { kind: 'thinking'; since: number }
  | { kind: 'model'; since: number };

/** The last call in `content` that has no result yet. */
function pendingCall(content: ContentBlock[]): ToolUseContent | null {
  const done = new Set<string>();
  for (const b of content) if (b.type === 'tool_result') done.add((b as { toolUseId: string }).toolUseId);
  for (let i = content.length - 1; i >= 0; i--) {
    const b = content[i];
    if (b.type === 'tool_use' && !done.has((b as ToolUseContent).id)) return b as ToolUseContent;
  }
  return null;
}

export function useStreamingWait(content: ContentBlock[], sessionId: string | undefined): StreamingWait | null {
  const quietSince = useRef(Date.now());
  const callSeen = useRef<{ id: string; at: number } | null>(null);
  const [now, setNow] = useState<number | null>(null); // null = not yet 45s quiet
  const { agents } = useBackgroundAgents(sessionId);

  useEffect(() => {
    quietSince.current = Date.now();
    setNow(null);
    let tick: ReturnType<typeof setInterval> | undefined;
    const arm = setTimeout(() => {
      setNow(Date.now());
      tick = setInterval(() => setNow(Date.now()), 1000);
    }, WAIT_HINT_MS);
    return () => { clearTimeout(arm); if (tick) clearInterval(tick); };
  }, [content]);

  const call = pendingCall(content);
  if (!call) callSeen.current = null;
  else if (callSeen.current?.id !== call.id) callSeen.current = { id: call.id, at: Date.now() };

  if (now === null) return null;
  if (call) {
    const v = stripView(call);
    return { kind: 'tool', tool: v.tool, object: v.object, since: callSeen.current!.at };
  }
  const running = agents.filter((a) => a.status === 'running');
  if (running.length) {
    const starts = running.map((a) => a.startedAt).filter((s): s is number => typeof s === 'number');
    return { kind: 'agents', count: running.length, since: starts.length ? Math.min(...starts) : quietSince.current };
  }
  const think = thinkingSince(sessionId, now);
  if (think !== null) return { kind: 'thinking', since: think };
  return { kind: 'model', since: quietSince.current };
}

/** 45s → "45s" / "45 秒"; 72s → "1m 12s" / "1 分 12 秒". */
export function formatWait(ms: number, zh: boolean): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (zh) return m ? `${m} 分 ${String(r).padStart(2, '0')} 秒` : `${r} 秒`;
  return m ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
}
