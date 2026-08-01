/**
 * Tests for resolveResumeTarget — the 4-action landing decision (A2 revised).
 * Actions: focus | newtab | reuse-current | needs-close.
 * Covers: already-open→focus, free-slot→newtab, reuse-current (ONLY the active
 * idle tab at cap), needs-close (full + active busy), the strict-idle rule, and
 * the Gate-1 CRITICAL: chatMax===1 with an idle active tab must NOT be locked out.
 */
import { describe, it, expect } from 'vitest';
import { resolveResumeTarget, isReusableIdle, type ResumeTabInfo } from '../resumeTarget';

const CHAT_MAX = 3;

function tab(id: string, opts: Partial<ResumeTabInfo> = {}): ResumeTabInfo {
  return { id, sessionId: opts.sessionId, status: opts.status ?? 'idle', isStreaming: opts.isStreaming ?? false };
}

describe('resolveResumeTarget', () => {
  it('already-open → focus that tab (even if a free slot exists)', () => {
    const tabs = [tab('t1', { sessionId: 'S1', status: 'streaming', isStreaming: true })];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX, 't1')).toEqual({ action: 'focus', tabId: 't1' });
  });

  it('free slot (< chatMax) → new tab', () => {
    const tabs = [tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true })];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX, 't1')).toEqual({ action: 'newtab' });
  });

  it('no free slot + ACTIVE tab idle → reuse-current (that active tab)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    // active tab is the idle one → reuse-current
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX, 't3')).toEqual({ action: 'reuse-current', tabId: 't3' });
  });

  it('no free slot + a BACKGROUND tab is idle but ACTIVE tab is busy → needs-close (NEVER reuse a background tab)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }), // active + busy
      tab('t2', { sessionId: 'B', status: 'idle' }),                          // background idle — must NOT be reused
      tab('t3', { sessionId: 'C', status: 'streaming', isStreaming: true }),
    ];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX, 't1')).toEqual({ action: 'needs-close' });
  });

  it('all tabs busy → needs-close', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'streaming', isStreaming: true }),
    ];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX, 't1')).toEqual({ action: 'needs-close' });
  });

  // ── Gate-1 CRITICAL regression: chatMax===1 single-tab user ──────────
  it('chatMax===1 + the one tab is idle+active → reuse-current (NOT locked out)', () => {
    const tabs = [tab('t1', { sessionId: 'X', status: 'idle' })];
    // slot full (1 tab, chatMax 1), active tab idle → reuse-current, NOT needs-close
    expect(resolveResumeTarget('S1', tabs, 1, 't1')).toEqual({ action: 'reuse-current', tabId: 't1' });
  });

  it('chatMax===1 + the one tab is busy → needs-close (correct: nothing free)', () => {
    const tabs = [tab('t1', { sessionId: 'X', status: 'streaming', isStreaming: true })];
    expect(resolveResumeTarget('S1', tabs, 1, 't1')).toEqual({ action: 'needs-close' });
  });

  it('a waiting_input / permission_needed / complete_unread / error ACTIVE tab is NOT reusable → needs-close', () => {
    for (const status of ['waiting_input', 'permission_needed', 'complete_unread', 'error']) {
      const tabs = [tab('t1', { sessionId: 'X', status })]; // !isStreaming but NOT idle
      expect(resolveResumeTarget('S1', tabs, 1, 't1')).toEqual({ action: 'needs-close' });
    }
  });

  it('isReusableIdle: only status===idle && !isStreaming', () => {
    expect(isReusableIdle(tab('x', { status: 'idle' }))).toBe(true);
    expect(isReusableIdle(tab('x', { status: 'idle', isStreaming: true }))).toBe(false);
    expect(isReusableIdle(tab('x', { status: 'waiting_input' }))).toBe(false);
  });

  it('already-open takes precedence over reuse-current at cap', () => {
    const tabs = [
      tab('t1', { sessionId: 'S1', status: 'idle' }),  // target already here + active
      tab('t2', { sessionId: 'B', status: 'idle' }),
    ];
    expect(resolveResumeTarget('S1', tabs, 2, 't1')).toEqual({ action: 'focus', tabId: 't1' });
  });

  it('no activeTabId provided + full → needs-close (cannot reuse an unknown active tab)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'idle' }),
      tab('t2', { sessionId: 'B', status: 'idle' }),
    ];
    expect(resolveResumeTarget('S1', tabs, 2)).toEqual({ action: 'needs-close' });
  });
});
