/**
 * Tests for resolveResumeTarget — the 4-branch "Resume in tab" decision.
 * Covers: already-open→focus, free-slot→newtab, reuse-idle, all-busy, and the
 * strict-idle rule (waiting_input/permission_needed/complete_unread/error are
 * NOT reusable even though !isStreaming).
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
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'focus', tabId: 't1' });
  });

  it('free slot (< chatMax) → new tab', () => {
    const tabs = [tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true })];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'newtab' });
  });

  it('no free slot but a strictly-idle tab → reuse it', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'reuse', tabId: 't3' });
  });

  it('all tabs busy (3 streaming) → busy (no tab change)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'streaming', isStreaming: true }),
    ];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'busy' });
  });

  it('a waiting_input / permission_needed / complete_unread / error tab is NOT reusable', () => {
    for (const status of ['waiting_input', 'permission_needed', 'complete_unread', 'error']) {
      const tabs = [
        tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
        tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
        tab('t3', { sessionId: 'C', status }), // !isStreaming but NOT idle
      ];
      // full → not newtab; not reusable → busy
      expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'busy' });
    }
  });

  it('isReusableIdle: only status===idle && !isStreaming', () => {
    expect(isReusableIdle(tab('x', { status: 'idle' }))).toBe(true);
    expect(isReusableIdle(tab('x', { status: 'idle', isStreaming: true }))).toBe(false);
    expect(isReusableIdle(tab('x', { status: 'waiting_input' }))).toBe(false);
  });

  it('already-open takes precedence over an idle tab at cap', () => {
    const tabs = [
      tab('t1', { sessionId: 'S1', status: 'idle' }),  // target already here
      tab('t2', { sessionId: 'B', status: 'idle' }),
      tab('t3', { sessionId: 'C', status: 'streaming', isStreaming: true }),
    ];
    expect(resolveResumeTarget('S1', tabs, CHAT_MAX)).toEqual({ action: 'focus', tabId: 't1' });
  });
});
