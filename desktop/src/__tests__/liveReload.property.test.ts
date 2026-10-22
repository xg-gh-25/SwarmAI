/**
 * Live == reload, across generated scenarios (the frontend half).
 *
 * The fixture (fixtures/liveReload/scenarios.json) is produced by the REAL backend
 * (backend/tests/test_live_reload_scenarios.py): per scenario, the steps a tab
 * sees — its own user bubbles, its permission decisions and answers, every
 * published session envelope — and the history JSON a reload returns.
 *
 * Each scenario is replayed through the real useChatStreamingLifecycle (the
 * session-subscription path, events parsed by the real parseSSEEvent), with the
 * tab's own writes mirrored from ChatPage (optimistic user bubble, decision /
 * answers written onto their block). Two views are compared with the reload
 * (toMessageCamelCase -> toDisplayMessage, as ChatPage loads a session):
 *
 * - STREAM: what the stream alone rendered (no timer runs: no reconcile, no mirror);
 * - RECONCILED: the stream view after MessageStore.reconcile(history), what a tab
 *   shows once its turn-end reconcile lands.
 *
 * Both for the tab that sends (T) and for another tab showing the same session (T2,
 * "other tab:" signatures): T2 gets every session event but none of T's own writes.
 *
 * Blocks are compared on what the user sees (type + text / ids / status /
 * decision), not on incidental fields. Every divergence gets a signature; the
 * report lists them all. KNOWN holds the signatures already accepted or queued
 * for a fix; a new signature fails the test.
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import fixture from './fixtures/liveReload/scenarios.json';
import type { Message, ContentBlock } from '../types';

const fake = vi.hoisted(() => {
  const listeners = new Set<(e: unknown) => void>();
  return {
    listeners,
    emit(e: unknown) { for (const l of listeners) l(e); },
  };
});
vi.mock('../services/sessionEvents', () => ({
  sessionEvents: {
    start: () => {},
    stop: () => {},
    isConnected: () => true,
    subscribe: (l: (e: unknown) => void) => { fake.listeners.add(l); return () => fake.listeners.delete(l); },
    onConnectionChange: () => () => {},
    watch: () => {},
    cursorFor: () => undefined,
  },
}));
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import { parseSSEEvent, toMessageCamelCase } from '../services/chat';
import { toDisplayMessage } from '../pages/chat/utils';
import { testTabMap, createMockDeps, initTestTab, resetTestState } from './helpers/streamingTestUtils';

type Step =
  | { kind: 'user'; clientId: string; text: string }
  | { kind: 'event'; env: Record<string, unknown> }
  | { kind: 'decide'; requestId: string; decision: 'approve' | 'deny' }
  | { kind: 'answer'; toolUseId: string; answers: Record<string, string> }
  | { kind: 'reconcile'; history: Record<string, unknown>[] };
interface Scenario { name: string; shape: string[]; steps: Step[]; history: Record<string, unknown>[] }

const SID = 'sess-lr';

/** What the user sees of one block. */
function seen(b: ContentBlock): string {
  const x = b as unknown as Record<string, unknown>;
  switch (x.type) {
    case 'text': return `text:${String(x.text ?? '').trim()}`;
    case 'thinking': return `thinking:${String(x.thinking ?? '').trim()}`;
    case 'tool_use': return `tool_use:${x.id}:${x.name}`;
    case 'tool_result': return `tool_result:${x.toolUseId}:${x.isError ? 'error' : 'ok'}`;
    case 'ask_user_question':
      return `question:${x.toolUseId}:${x.answers ? `answered=${JSON.stringify(x.answers)}` : 'open'}`;
    case 'cmd_permission_request': return `permission:${x.requestId}:${x.decision ?? 'undecided'}`;
    case 'background_finished':
      return `background_finished:${((x.agents as { toolUseId: string }[]) ?? []).map((a) => a.toolUseId).join(',')}`;
    default: return String(x.type);
  }
}
const view = (ms: readonly Message[]) =>
  ms.map((m) => ({ role: m.role, blocks: (m.content ?? []).map(seen),
    source: String((m.metadata as { turn_source?: string } | undefined)?.turn_source ?? '') }));

/** Signatures of how *live* differs from *reload* (both from view()). */
function divergences(live: ReturnType<typeof view>, reload: ReturnType<typeof view>): string[] {
  const out = new Set<string>();
  const roles = (v: typeof live) => v.map((m) => m.role[0]).join('');
  const collapse = (v: typeof live) => v.filter((m) => !(m.role === 'assistant' && m.blocks.length === 0));
  if (live.some((m) => m.role === 'assistant' && m.blocks.length === 0)) out.add('live: empty assistant bubble');
  const l = collapse(live);
  if (roles(l) !== roles(reload)) {
    const la = l.filter((m) => m.role === 'assistant').length;
    const ra = reload.filter((m) => m.role === 'assistant').length;
    const lu = l.filter((m) => m.role === 'user').length;
    const ru = reload.filter((m) => m.role === 'user').length;
    if (lu !== ru) out.add(`user bubbles: live ${lu > ru ? 'more' : 'fewer'} than reload`);
    if (la < ra) out.add('assistant bubbles: live fewer than reload');
    // an assistant bubble right after another one: the reload has them as one
    l.forEach((m, i) => {
      if (i > 0 && m.role === 'assistant' && l[i - 1].role === 'assistant') {
        out.add(`assistant bubbles: a ${m.source || 'unlabelled'} turn has its own bubble`);
      }
    });
    if (lu === ru && la === ra) out.add('bubble order differs');
  }
  const flat = (v: typeof live) => v.flatMap((m) => m.blocks.map((b) => `${m.role}|${b}`));
  const lf = flat(l), rf = flat(reload);
  const count = (xs: string[]) => xs.reduce((m, x) => m.set(x, (m.get(x) ?? 0) + 1), new Map<string, number>());
  const lc = count(lf), rc = count(rf);
  const kind = (k: string) => k.split('|')[1].split(':')[0];
  for (const [k, n] of lc) {
    const r = rc.get(k) ?? 0;
    if (n > r) out.add(r === 0 ? `only live: ${kind(k)}` : `live repeats: ${kind(k)}`);
  }
  const STATEFUL = new Set(['tool_result', 'question', 'permission']);
  for (const [k, n] of rc) {
    const lv = lc.get(k) ?? 0;
    if (n <= lv) continue;
    const kd = kind(k);
    if (STATEFUL.has(kd)) {
      // the same block with another status / decision / answer state?
      const stem = k.split(':').slice(0, -1).join(':');
      const other = [...lc.keys()].find((x) => x !== k && x.startsWith(stem + ':'));
      if (other) { out.add(`state differs: ${kd} (live ${other.split(':').pop()}, reload ${k.split(':').pop()})`); continue; }
    }
    out.add(lv === 0 ? `only reload: ${kd}` : `live drops a repeated ${kd}`);
  }
  if (out.size === 0 && lf.join('\n') !== rf.join('\n')) out.add('block order differs');
  return [...out];
}

function replay(s: Scenario, renderLifecycle: () => void, withReconcile: boolean) {
  resetTestState();
  fake.listeners.clear();
  renderLifecycle();
  act(() => { initTestTab('T'); initTestTab('T2'); });
  testTabMap.get('T')!.sessionId = SID;
  testTabMap.get('T2')!.sessionId = SID;   // another tab showing the same session; it sends nothing
  const store = messageStoreRegistry.getOrCreate('T', { sessionId: SID });
  const store2 = messageStoreRegistry.getOrCreate('T2', { sessionId: SID });
  for (const st of s.steps) {
    if (st.kind === 'user') {
      act(() => {
        store.append({ id: st.clientId, role: 'user', content: [{ type: 'text', text: st.text }] as ContentBlock[],
          timestamp: new Date().toISOString(), metadata: { client_id: st.clientId } } as Message);
      });
    } else if (st.kind === 'reconcile') {
      // the turn-end reconcile (useChatStreamingLifecycle scheduleTurnEndReconcile)
      if (withReconcile) {
        act(() => {
          store.reconcile(st.history.map((r) => toMessageCamelCase(r)));
          store2.reconcile(st.history.map((r) => toMessageCamelCase(r)));
        });
      }
    } else if (st.kind === 'event') {
      act(() => { fake.emit(parseSSEEvent(JSON.stringify(st.env))); });
    } else if (st.kind === 'decide') {
      // ChatPage.handlePermissionDecision: the decision onto its block
      act(() => {
        store.updateLast((m) => ({ ...m, content: m.content.map((b) => {
          const x = b as unknown as { type: string; requestId?: string };
          return x.type === 'cmd_permission_request' && x.requestId === st.requestId ? { ...b, decision: st.decision } as ContentBlock : b;
        }) }), (m) => m.content.some((b) => (b as unknown as { requestId?: string }).requestId === st.requestId));
      });
    } else {
      // ChatPage.handleAnswerQuestion: the answers onto its block
      act(() => {
        store.updateLast((m) => ({ ...m, content: m.content.map((b) => {
          const x = b as unknown as { type: string; toolUseId?: string };
          return x.type === 'ask_user_question' && x.toolUseId === st.toolUseId ? { ...b, answers: st.answers } as ContentBlock : b;
        }) }), (m) => m.content.some((b) => (b as unknown as { toolUseId?: string; type: string }).type === 'ask_user_question'
          && (b as unknown as { toolUseId?: string }).toolUseId === st.toolUseId));
      });
    }
  }
  return [store, store2] as const;
}

/**
 * Divergences found by this test (2026-10-22, first run), by root cause. Each is a
 * real live != reload difference, not accepted as correct: listed so the test is
 * a ratchet (a NEW divergence fails it) while these are fixed one root at a time.
 * Remove a line when its root is fixed; the test then holds it.
 */
const KNOWN = new Set<string>([
  // R2 — ask_user_question is never stored (core/permission_prompt stores only
  // permission prompts): after a reload the question and its answer are gone.
  'stream: only live: question',
  'reconciled: only live: question',
  'stream: other tab: only live: question',
  'reconciled: other tab: only live: question',
  // R4 — user messages are not session events: another tab showing the session
  // has no user bubbles until its reconcile, and consecutive turns run together.
  // (S1: the user message is a transcript record with a rev, like any block.)
  'stream: other tab: user bubbles: live fewer than reload',
  'stream: other tab: only reload: text',
  'stream: other tab: assistant bubbles: a chat turn has its own bubble',
  // R5 — a permission decision made in one tab is not published: the other tab
  // keeps the card undecided until its reconcile. (S1: the decision is an update
  // with a rev, announced by transcript_rev.)
  'stream: other tab: only live: permission',
  'stream: other tab: state differs: permission (live undecided, reload deny)',
  'stream: other tab: state differs: permission (live undecided, reload approve)',
]);

const scenarios = (fixture as { scenarios: Scenario[] }).scenarios;
const report: { stream: Map<string, string[]>; reconciled: Map<string, string[]> } = {
  stream: new Map(), reconciled: new Map(),
};
const note = (m: Map<string, string[]>, sig: string, name: string) => m.set(sig, [...(m.get(sig) ?? []), name]);

describe('live == reload (generated scenarios from the real backend)', () => {
  beforeAll(() => { vi.useFakeTimers(); });   // no timer fires: the stream view is the stream alone
  afterAll(() => {
    vi.useRealTimers();
    const lines: string[] = [];
    for (const [mode, m] of Object.entries(report)) {
      lines.push(`== ${mode}: ${m.size} signature(s)`);
      for (const [sig, names] of [...m.entries()].sort((a, b) => b[1].length - a[1].length)) {
        lines.push(`  ${names.length.toString().padStart(3)}  ${sig}   e.g. ${names.slice(0, 3).join(' ')}`);
      }
    }
    // eslint-disable-next-line no-console
    console.log(`\n[liveReload]\n${lines.join('\n')}\n`);
  });

  it.each(scenarios.map((s) => [s.name, s] as const))('%s', (_name, s) => {
    const reloadMsgs = s.history.map((r) => toDisplayMessage(toMessageCamelCase(r) as never));
    const reload = view(reloadMsgs);
    const run = (withReconcile: boolean) => {
      const [store, store2] = replay(s, () => { renderHook(() => useChatStreamingLifecycle(createMockDeps())); }, withReconcile);
      const v = view(store.messages);
      if (process.env.LR_DUMP === s.name) {
        // eslint-disable-next-line no-console
        console.log(withReconcile ? 'RECONCILED' : 'STREAM', JSON.stringify({ live: v, other: view(store2.messages), reload }));
      }
      return [...divergences(v, reload), ...divergences(view(store2.messages), reload).map((x) => `other tab: ${x}`)];
    };
    const stream = run(false);
    for (const sig of stream) note(report.stream, sig, s.name);
    const reconciled = run(true);
    for (const sig of reconciled) note(report.reconciled, sig, s.name);
    const unknown = [...stream.map((x) => `stream: ${x}`), ...reconciled.map((x) => `reconciled: ${x}`)]
      .filter((x) => !KNOWN.has(x));
    expect(unknown, `${s.name} ${s.shape.join(' > ')}`).toEqual([]);
  });
});
