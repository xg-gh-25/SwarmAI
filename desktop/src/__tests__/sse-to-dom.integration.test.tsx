/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * E2E assembly integration — SSE → MessageStore → DOM.
 *
 * THE GAP THIS CLOSES: three tests each covered ONE link of the chat render
 * chain, but none drove the WHOLE chain end-to-end:
 *   - chat.contract.test.ts        — parseSSEEvent ✓  (no render)
 *   - AssistantMessageView.renderFidelity — DOM render ✓  (static message, no SSE, no store)
 *   - useMessageStore.integration  — store → React state ✓  (no parseSSEEvent, no DOM component)
 *
 * This test wires the REAL links together: a raw SSE `data:` line is fed
 * through the REAL parseSSEEvent, its content blocks are appended/streamed into
 * a REAL MessageStore exactly as useChatStreamingLifecycle's hot path does
 * (store.append placeholder → store.updateLast per text_delta → endStreaming),
 * the store's getSnapshot() output is then rendered by the REAL
 * AssistantMessageView, and we assert the FULL streamed content reached the DOM.
 *
 * Why this is the OT01 content-loss guard at the SYSTEM level: the per-link
 * tests prove each link in isolation, but a content-loss bug can live in the
 * SEAM between links (parse drops a block, store assembly mis-keys, render
 * gate hides a block). Only an end-to-end assembly test sees the seam.
 *
 * NO MOCK of the subject: parseSSEEvent, MessageStore, AssistantMessageView are
 * all the real implementations. Only the heavy leaf children (i18n, HealthContext)
 * are stubbed, mirroring AssistantMessageView.renderFidelity.test.tsx — those are
 * NOT part of the assembly path under test (GUI32/PIT13: drive the real path,
 * mock only the boundary).
 *
 * Content-STRUCTURAL assertions (distinctive tokens reach the DOM, full length
 * survives), never pixel/exact-AI-text — cannot flake on output variance.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { parseSSEEvent } from '../services/chat';
import { MessageStore } from '../stores/MessageStore';
import { applyTextDelta } from '../hooks/useChatStreamingLifecycle';
import { AssistantMessageView } from '../pages/chat/components/AssistantMessageView';
import { ToastProvider } from '../contexts/ToastContext';
import type { Message } from '../types';

// ── Mocks: heavy leaf children only (mirror renderFidelity) — NOT the assembly path ──
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));
vi.mock('../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

// applyTextDelta is the REAL hot-path token-application function, imported from
// useChatStreamingLifecycle (not a local copy) — so this test can never drift
// from the production delta logic (Gate-2 INFO hardening, run_2097cdc9).

function makePlaceholder(id: string): Message {
  return { id, role: 'assistant', content: [], timestamp: new Date().toISOString() };
}

/** Render whatever the store currently holds, exactly as TabView feeds the view. */
function renderFromStore(store: MessageStore) {
  const msgs = store.getSnapshot();
  const asst = [...msgs].reverse().find((m) => m.role === 'assistant')!;
  return render(
    <ToastProvider>
      <AssistantMessageView message={asst} isStreaming={store.phase === 'streaming'} />
    </ToastProvider>,
  );
}

describe('E2E assembly — SSE → MessageStore → DOM', () => {
  it('streams text deltas through the store and renders the FULL accumulated answer', () => {
    const store = new MessageStore();
    const asstId = 'asst-e2e-1';

    // 1. assistant placeholder appended (as the hot path does on assistant-start)
    store.append(makePlaceholder(asstId));
    store.startStreaming(asstId);

    // 2. backend streams the answer as token deltas (real SSE `data:` lines →
    //    real parseSSEEvent). Each carries a text_delta the hot path applies.
    const deltas = [
      'E2E_HEAD_alpha the answer begins ',
      'with a middle that is reasonably long '.repeat(10),
      'E2E_TAIL_omega and ends here.',
    ];
    for (const text of deltas) {
      const raw = JSON.stringify({ type: 'text_delta', text });
      const ev = parseSSEEvent(raw) as any;
      expect(ev.type).toBe('text_delta'); // real parser handled it
      store.updateLast(
        (m) => applyTextDelta(m, ev.text),
        (m) => m.id === asstId,
      );
    }
    store.endStreaming();

    // 3. render the store's snapshot through the REAL view
    renderFromStore(store);

    // HEAD + TAIL both in the DOM ⇒ no block dropped, no mid-stream truncation
    const node = screen.getByText(/E2E_HEAD_alpha/);
    expect(node.textContent).toContain('E2E_TAIL_omega');
    // full accumulated length survived the whole chain
    const expectedLen = deltas.join('').length;
    expect(store.getSnapshot().find((m) => m.id === asstId)!.content
      .reduce((n, b: any) => n + (b.text?.length ?? 0), 0)).toBe(expectedLen);
  });

  it('renders a multi-block assistant event (text + tool_use + post-tool text) parsed from a real SSE assistant frame', () => {
    // The agentic loop emits an `assistant` event whose content array interleaves
    // text and tool blocks. parseSSEEvent camelCases it; the store holds it; the
    // view must render every TEXT block — the post-tool text is the OT01-prone one.
    const store = new MessageStore();
    const asstId = 'asst-e2e-2';

    const raw = JSON.stringify({
      type: 'assistant',
      session_id: 's1',
      content: [
        { type: 'text', text: 'PRE_TOOL_zeta reasoning before the call' },
        { type: 'tool_use', id: 'tu_1', name: 'Read', input: { file_path: '/tmp/x' } },
        { type: 'tool_result', tool_use_id: 'tu_1', content: 'ok', is_error: false },
        { type: 'text', text: 'POST_TOOL_yota the conclusion after the tool' },
      ],
    });
    const ev = parseSSEEvent(raw) as any;
    // real parser converted snake_case → camelCase on the tool_result block
    const tr = (ev.content as any[]).find((b) => b.type === 'tool_result');
    expect(tr.toolUseId).toBe('tu_1');
    expect(tr.isError).toBe(false);

    store.append({ id: asstId, role: 'assistant', content: ev.content, timestamp: new Date().toISOString() });
    renderFromStore(store);

    // both the pre-tool AND the post-tool text blocks survive assembly + render
    expect(screen.getByText(/PRE_TOOL_zeta/)).toBeTruthy();
    expect(screen.getByText(/POST_TOOL_yota/)).toBeTruthy();
  });
});
