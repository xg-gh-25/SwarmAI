/**
 * thinkingProgressStore — when each session's model started its current think.
 *
 * The backend sends `thinking_progress` (with `elapsed_seconds`) in place of the 15s
 * heartbeat while the model is in extended thinking and has written no text yet
 * (routers/chat.py). Those events write no message content, so the only way the
 * streaming wait hint can tell "the model is thinking" from "waiting for the model"
 * is this side record. Not reactive: the hint reads it on its own 1s tick.
 *
 * A record counts only while fresh (seen within THINKING_FRESH_MS, a little over the
 * 15s heartbeat): once the model writes text the backend stops sending it.
 *
 * @exports noteThinking, thinkingSince, clearThinking, THINKING_FRESH_MS
 */

export const THINKING_FRESH_MS = 20_000;

const bySession = new Map<string, { since: number; seenAt: number }>();

/** Record a `thinking_progress` event for `sessionId`. */
export function noteThinking(sessionId: string, elapsedSeconds: unknown, now = Date.now()): void {
  const elapsed = typeof elapsedSeconds === 'number' && Number.isFinite(elapsedSeconds) && elapsedSeconds >= 0
    ? elapsedSeconds : 0;
  bySession.set(sessionId, { since: now - elapsed * 1000, seenAt: now });
}

/** When the current think started, or null when the session is not thinking now. */
export function thinkingSince(sessionId: string | undefined, now = Date.now()): number | null {
  if (!sessionId) return null;
  const r = bySession.get(sessionId);
  return r && now - r.seenAt <= THINKING_FRESH_MS ? r.since : null;
}

/** Test helper / turn reset. */
export function clearThinking(sessionId?: string): void {
  if (sessionId) bySession.delete(sessionId);
  else bySession.clear();
}
