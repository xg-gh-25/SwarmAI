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
// Resume-shaped guard: no draft guard, reuse-current allowed (it reloads a session
// after clearing, so reusing an idle tab loses nothing).
const NO_GUARD = { hasDraft: false, applyDraftGuard: false, allowReuseCurrent: true };
// Dispatch-shaped guard: draft guard on, reuse-current OFF (dispatch = new work,
// must not wipe a history-bearing idle tab — the convergence decision).
const DISPATCH_GUARD = { hasDraft: false, applyDraftGuard: true, allowReuseCurrent: false };

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
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', { hasDraft: true, applyDraftGuard: true, allowReuseCurrent: false }))
      .toEqual({ kind: 'blocked', reason: 'draft' });
  });

  it('draft guard is OPT-IN: applyDraftGuard=false ignores a draft → still reuse (resume behavior unchanged)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', { hasDraft: true, applyDraftGuard: false, allowReuseCurrent: true }))
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

  // ── Dispatch convergence (allowReuseCurrent=false): never wipe a history-bearing
  //    idle tab; reuse only a genuinely empty one; else blocked:occupied. ──────────
  it('dispatch at cap + active idle tab WITH history → blocked/occupied (never silently wiped)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }), // idle but holds a conversation
    ];
    expect(classifyLanding('__disp__', tabs, CHAT_MAX, 't3', DISPATCH_GUARD))
      .toEqual({ kind: 'blocked', reason: 'occupied' });
  });

  it('dispatch at cap + active idle tab EMPTY (no session) → reuse (no history to lose; chatMax===1 unblocked)', () => {
    const tabs = [tab('t1', { status: 'idle' })]; // no sessionId → empty
    expect(classifyLanding('__disp__', tabs, 1, 't1', DISPATCH_GUARD))
      .toEqual({ kind: 'land', mode: 'reuse', tabId: 't1' });
  });

  // ── MUTATION-PROVEN guards for the occupied branch (the 4 tests above were
  //    mostly characterization: only the WITH-history one dies when the branch is
  //    deleted). These two discriminators pin BOTH free variables of the new
  //    decision (sessionId presence, allowReuseCurrent flag) so reverting EITHER
  //    the `target?.sessionId` check OR the `!allowReuseCurrent` guard goes RED. ──
  it('MUTATION GUARD — sessionId is the discriminator: same at-cap dispatch, history→occupied vs empty→reuse', () => {
    const withHistory = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    const empty = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { status: 'idle' }), // idle, NO sessionId
    ];
    // Identical shape/flag; ONLY t3.sessionId differs → different verdict.
    expect(classifyLanding('__disp__', withHistory, CHAT_MAX, 't3', DISPATCH_GUARD))
      .toEqual({ kind: 'blocked', reason: 'occupied' });
    expect(classifyLanding('__disp__', empty, CHAT_MAX, 't3', DISPATCH_GUARD))
      .toEqual({ kind: 'land', mode: 'reuse', tabId: 't3' });
  });

  it('MUTATION GUARD — allowReuseCurrent is the discriminator: same history-bearing tab, false→occupied vs true→reuse', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    // Identical tabs; ONLY the flag differs → dispatch blocks, resume reuses.
    expect(classifyLanding('__disp__', tabs, CHAT_MAX, 't3', { hasDraft: false, applyDraftGuard: true, allowReuseCurrent: false }))
      .toEqual({ kind: 'blocked', reason: 'occupied' });
    expect(classifyLanding('__disp__', tabs, CHAT_MAX, 't3', { hasDraft: false, applyDraftGuard: true, allowReuseCurrent: true }))
      .toEqual({ kind: 'land', mode: 'reuse', tabId: 't3' });
  });

  // CHARACTERIZATION (not occupied-branch coverage): documents that a free slot
  // still prefers newtab — resolveResumeTarget never reaches reuse-current here.
  it('dispatch free slot → newtab (convergence: prefer a fresh tab, never touch existing)', () => {
    const tabs = [tab('t1', { sessionId: 'A', status: 'idle' })];
    expect(classifyLanding('__disp__', tabs, CHAT_MAX, 't1', DISPATCH_GUARD))
      .toEqual({ kind: 'land', mode: 'newtab' });
  });

  // PARITY GUARD (resume path unchanged) — NOT occupied-branch coverage; the
  // allowReuseCurrent=true block is skipped by design. Kept to prevent a future
  // change from accidentally blocking resume on a history-bearing idle tab.
  it('resume (allowReuseCurrent=true) still reuses a history-bearing idle tab at cap (reloads after clear)', () => {
    const tabs = [
      tab('t1', { sessionId: 'A', status: 'streaming', isStreaming: true }),
      tab('t2', { sessionId: 'B', status: 'streaming', isStreaming: true }),
      tab('t3', { sessionId: 'C', status: 'idle' }),
    ];
    expect(classifyLanding('__k__', tabs, CHAT_MAX, 't3', NO_GUARD))
      .toEqual({ kind: 'land', mode: 'reuse', tabId: 't3' });
  });
});

// ---------------------------------------------------------------------------
// classifyWorkLanding — work landing with the pinned Swarm Out tab out of play
// ---------------------------------------------------------------------------
import { classifyWorkLanding } from '../landPromptInTab';
import { SWARM_OUT_TAB_ID } from '../swarmOut';

describe('classifyWorkLanding (Swarm Out never reused, never counted)', () => {
  const so = (opts: Partial<ResumeTabInfo> = {}) => tab(SWARM_OUT_TAB_ID, opts);

  it('Swarm Out does not count against the user: 1 user tab + Swarm Out, chat_max 3 → newtab', () => {
    const tabs = [tab('t1', { sessionId: 'A' }), so({ sessionId: 'S' })];
    expect(classifyWorkLanding('__k__', tabs, 3, 't1', DISPATCH_GUARD)).toEqual({ kind: 'land', mode: 'newtab' });
  });

  it('user tabs at their cap (chat_max - 1) → no newtab', () => {
    const tabs = [tab('t1', { sessionId: 'A' }), tab('t2', { sessionId: 'B' }), so()];
    expect(classifyWorkLanding('__k__', tabs, 3, 't2', NO_GUARD)).toEqual({ kind: 'land', mode: 'reuse', tabId: 't2' });
  });

  it('Swarm Out active and idle at the cap → never reused (blocked instead)', () => {
    const tabs = [tab('t1', { sessionId: 'A' }), tab('t2', { sessionId: 'B' }), so({ status: 'idle' })];
    expect(classifyWorkLanding('__k__', tabs, 3, SWARM_OUT_TAB_ID, NO_GUARD)).toEqual({ kind: 'blocked', reason: 'cap' });
  });

  it("resuming Swarm Out's own session focuses it", () => {
    const tabs = [tab('t1', { sessionId: 'A' }), so({ sessionId: 'S' })];
    expect(classifyWorkLanding('S', tabs, 3, 't1', NO_GUARD)).toEqual({ kind: 'land', mode: 'focus', tabId: SWARM_OUT_TAB_ID });
  });

  it('a user session already open is still focused', () => {
    const tabs = [tab('t1', { sessionId: 'A' }), so({ sessionId: 'S' })];
    expect(classifyWorkLanding('A', tabs, 3, SWARM_OUT_TAB_ID, NO_GUARD)).toEqual({ kind: 'land', mode: 'focus', tabId: 't1' });
  });
});

describe('classifyWorkLanding from the Swarm Out tab at the floor (one user tab)', () => {
  const so = () => tab(SWARM_OUT_TAB_ID, { sessionId: 'S' });

  it('reuses the empty idle user tab instead of blocking', () => {
    const tabs = [tab('t1'), so()];
    expect(classifyWorkLanding('__k__', tabs, 2, SWARM_OUT_TAB_ID, DISPATCH_GUARD)).toEqual({ kind: 'land', mode: 'reuse', tabId: 't1' });
  });

  it('never clears a user tab that holds a conversation', () => {
    const tabs = [tab('t1', { sessionId: 'A' }), so()];
    expect(classifyWorkLanding('__k__', tabs, 2, SWARM_OUT_TAB_ID, NO_GUARD).kind).toBe('blocked');
  });

  it('never clears a user tab with an unsent draft', () => {
    const tabs = [tab('t1'), so()];
    expect(classifyWorkLanding('__k__', tabs, 2, SWARM_OUT_TAB_ID, DISPATCH_GUARD, (id) => id === 't1').kind).toBe('blocked');
  });

  it('never clears a busy user tab', () => {
    const tabs = [tab('t1', { status: 'streaming', isStreaming: true }), so()];
    expect(classifyWorkLanding('__k__', tabs, 2, SWARM_OUT_TAB_ID, DISPATCH_GUARD).kind).toBe('blocked');
  });
});
