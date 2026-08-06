/**
 * Tests for classifyLanding — the PURE landing verdict that unifies the tab-landing
 * prefix the 3 ChatPage handlers used to each re-implement. It wraps
 * resolveResumeTarget and folds in the (opt-in) unsent-draft guard, returning a
 * side-effect-free verdict the caller executes.
 *
 * Verdicts:
 *   { kind:'land', mode:'focus'|'newtab'|'reuse', tabId? }
 *   { kind:'blocked', reason:'cap'|'busy'|'draft', busyStatus? }
 *
 * Key behaviors under test:
 *   - newtab-first (free slot) even when the active tab is idle
 *   - focus is preserved (already-open session) — resume relies on it
 *   - at cap + active idle → reuse; + hasDraft & applyDraftGuard → blocked:draft
 *   - draft guard is OPT-IN: applyDraftGuard=false (resume) never blocks on a draft
 *   - at cap + active busy → blocked:busy carrying the busyStatus (streaming vs other)
 *   - at cap + no reusable active tab → blocked:cap
 */
import { describe, it, expect } from 'vitest';
import { classifyLanding } from '../landPromptInTab';
import type { ResumeTabInfo } from '../resumeTarget';

const CHAT_MAX = 3;
function tab(id: string, opts: Partial<ResumeTabInfo> = {}): ResumeTabInfo {
  return { id, sessionId: opts.sessionId, status: opts.status ?? 'idle', isStreaming: opts.isStreaming ?? false };
}
const NO_GUARD = { hasDraft: false, applyDraftGuard: false };

describe('classifyLanding', () => {
  it('free slot → land/newtab (even when the active tab is idle)', () => {
    const tabs = [tab('t1', { sessionId: 'A', status: 'idle' })];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't1', NO_GUARD)).toEqual({ kind: 'land', mode: 'newtab' });
  });

  it('already-open session → land/focus that tab (resume relies on this)', () => {
    const tabs = [tab('t1', { sessionId: 'S1', status: 'streaming', isStreaming: true })];
    expect(classifyLanding('S1', tabs, CHAT_MAX, 't1', NO_GUARD)).toEqual({ kind: 'land', mode: 'focus', tabId: 't1' });
  });

  it('at cap + active idle + no draft → land/reuse that tab', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', NO_GUARD)).toEqual({ kind: 'land', mode: 'reuse', tabId: 't3' });
  });

  it('at cap + active idle + hasDraft + applyDraftGuard → blocked/draft', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', { hasDraft: true, applyDraftGuard: true }))
      .toEqual({ kind: 'blocked', reason: 'draft' });
  });

  it('draft guard is OPT-IN: applyDraftGuard=false ignores a draft → still reuse (resume behavior unchanged)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', { hasDraft: true, applyDraftGuard: false }))
      .toEqual({ kind: 'land', mode: 'reuse', tabId: 't3' });
  });

  it('at cap + active tab STREAMING → blocked/busy with busyStatus=streaming', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'idle' }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    // active tab t1 is streaming → not reusable, no free slot → blocked:busy(streaming)
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't1', NO_GUARD))
      .toEqual({ kind: 'blocked', reason: 'busy', busyStatus: 'streaming' });
  });

  it('at cap + active tab WAITING_INPUT → blocked/busy with busyStatus=waiting_input', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'waiting_input' }),
      tab('t2', { sessionId: 'B', status: 'idle' }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't1', NO_GUARD))
      .toEqual({ kind: 'blocked', reason: 'busy', busyStatus: 'waiting_input' });
  });

  it('at cap + NO active tab id (unknown) → blocked/cap', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'streaming', isStreaming: true }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, undefined, NO_GUARD)).toEqual({ kind: 'blocked', reason: 'cap' });
  });

  it('chatMax===1 + single idle active tab → reuse (never locked out — Gate-1 CRITICAL parity)', () => {
    const tabs = [tab('t1', { sessionId: 'A', status: 'idle' })];
    expect(classifyLanding('__k__', tabs, 1, 't1', NO_GUARD)).toEqual({ kind: 'land', mode: 'reuse', tabId: 't1' });
  });
});
