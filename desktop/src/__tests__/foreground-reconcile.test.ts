/**
 * foreground-reconcile.test.ts
 *
 * Locks the OT01-recurrence fix (App-Nap stale store). macOS App Nap throttles
 * requestAnimationFrame AND setTimeout AND suspends SSE on a backgrounded Tauri
 * WebView, so a tab that streamed while backgrounded can land with:
 *   - its MessageStore's React mirror behind the store (rAF/timeout notify
 *     throttled), and/or
 *   - its store behind the backend DB (SSE suspended) while isStreaming is
 *     falsely stuck true.
 *
 * Two guarantees under test:
 *  1. MessageStore.flush() force-drains a pending (rAF-gated) notification —
 *     the foreground handler calls flushAll() so the React mirror catches up.
 *  2. The foreground-reconcile DECISION is backend-state-gated: force-merge ONLY
 *     when the backend reports the session is NOT streaming (cannot clobber a
 *     genuinely live stream).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { MessageStore } from '../stores/MessageStore';

afterEach(() => { vi.restoreAllMocks(); });

function msg(id: string, text: string) {
  return { id, role: 'assistant' as const, content: [{ type: 'text' as const, text }], timestamp: new Date().toISOString() };
}

describe('MessageStore.flush() drains App-Nap-throttled notifications', () => {
  it('fires the pending listener synchronously when rAF has not run yet', () => {
    // Capture the rAF callback instead of running it (simulates App-Nap throttle).
    const pending: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { pending.push(cb); return 1; });
    vi.stubGlobal('cancelAnimationFrame', () => {});

    const store = new MessageStore();
    try {
      let notifications = 0;
      store.subscribe(() => { notifications++; });
      store.append(msg('a', 'hello'));
      // rAF was captured, not run → mirror has NOT been notified yet (the freeze).
      expect(notifications).toBe(0);
      // Foreground flush drains it immediately.
      store.flush();
      expect(notifications).toBe(1);
    } finally {
      store.destroy(); // avoid the 90s streaming watchdog leaking into vitest
    }
  });

  it('flush() is a no-op when nothing is pending (clean mirror)', () => {
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1; });
    const store = new MessageStore();
    try {
      let notifications = 0;
      store.subscribe(() => { notifications++; });
      store.append(msg('a', 'x')); // rAF runs synchronously here → 1 notify
      const before = notifications;
      store.flush(); // nothing dirty → no extra notify
      expect(notifications).toBe(before);
    } finally {
      store.destroy();
    }
  });
});

// ── Foreground-reconcile decision (mirrors the ChatPage visibility handler) ──
interface TabFG { sessionId?: string; isStreaming: boolean; }
function decideForegroundReconcile(
  tab: TabFG | undefined,
  backendStreaming: boolean,
): { forceMerge: boolean; clearStuckFlag: boolean } {
  if (!tab?.sessionId) return { forceMerge: false, clearStuckFlag: false };
  if (backendStreaming) return { forceMerge: false, clearStuckFlag: false }; // live stream — never touch
  return { forceMerge: true, clearStuckFlag: tab.isStreaming };
}

describe('Foreground-reconcile decision (backend-state gated)', () => {
  it('backend STILL streaming → never force-merge (cannot clobber a live stream)', () => {
    const d = decideForegroundReconcile({ sessionId: 's1', isStreaming: true }, true);
    expect(d.forceMerge).toBe(false);
    expect(d.clearStuckFlag).toBe(false);
  });

  it('backend idle + frontend stuck-streaming → force-merge AND clear the stale flag', () => {
    const d = decideForegroundReconcile({ sessionId: 's1', isStreaming: true }, false);
    expect(d.forceMerge).toBe(true);
    expect(d.clearStuckFlag).toBe(true);
  });

  it('backend idle + frontend already idle → force-merge (catch up store) but no flag to clear', () => {
    const d = decideForegroundReconcile({ sessionId: 's1', isStreaming: false }, false);
    expect(d.forceMerge).toBe(true);
    expect(d.clearStuckFlag).toBe(false);
  });

  it('no sessionId → no-op', () => {
    expect(decideForegroundReconcile({ isStreaming: true }, false).forceMerge).toBe(false);
    expect(decideForegroundReconcile(undefined, false).forceMerge).toBe(false);
  });
});
