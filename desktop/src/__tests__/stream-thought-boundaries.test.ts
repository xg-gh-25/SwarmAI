/**
 * The REAL stream handler keeps every thought (XG 2026-10-18).
 *
 * What is tested (useChatStreamingLifecycle's createStreamHandler writing to the
 * tab's MessageStore, driven by the SSE event shapes the backend emits):
 * - `thinking_start` opens a new block: two thoughts whose confirmations arrive
 *   late are still two blocks, each confirmed in place
 * - `text_start` does the same for text
 * - the agentic sequence thinking → tool → thinking → text keeps every block
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import type { ContentBlock, StreamEvent } from '../types';
import { createMockDeps, initTestTab, makeMessage, resetTestState } from './helpers/streamingTestUtils';

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

const TAB = 'tab-thoughts';
const MSG = 'msg-thoughts';
const TH1 = 'First read the router to see how sessions are keyed.';
const TH2 = 'Now the unit, to check that the key is the same.';

function setup() {
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  act(() => {
    initTestTab(TAB);
    result.current.setIsStreaming(true);
    messageStoreRegistry.getOrCreate(TAB, { sessionId: 's-th' })
      .replace([makeMessage({ id: MSG, role: 'assistant', content: [] })]);
  });
  const handler = result.current.createStreamHandler(MSG, TAB);
  const send = (...events: StreamEvent[]) => act(() => { events.forEach((e) => handler(e)); });
  const content = () => messageStoreRegistry.getOrCreate(TAB).getSnapshot().find((m) => m.id === MSG)!.content;
  const shape = () => content().map((b) => (b.type === 'thinking' ? `th:${(b as { thinking: string }).thinking}`
    : b.type === 'text' ? `tx:${(b as { text: string }).text}` : b.type));
  return { send, content, shape };
}
const deltas = (type: 'thinking_delta' | 'text_delta', s: string): StreamEvent[] =>
  s.match(/.{1,9}/g)!.map((tok) => (type === 'thinking_delta'
    ? { type, thinking: tok } : { type, text: tok }) as StreamEvent);
const assistant = (...blocks: ContentBlock[]): StreamEvent => ({ type: 'assistant', content: blocks } as StreamEvent);

describe('stream handler: block boundaries keep every thought', () => {
  beforeEach(() => resetTestState());

  it('two thoughts back to back stay two blocks even when both confirmations come late', () => {
    const { send, shape } = setup();
    send({ type: 'thinking_start', index: 0 } as StreamEvent, ...deltas('thinking_delta', TH1));
    send({ type: 'thinking_start', index: 1 } as StreamEvent, ...deltas('thinking_delta', TH2));
    // what the user sees BEFORE any confirmation: two thoughts, not one merged block
    expect(shape()).toEqual([`th:${TH1}`, `th:${TH2}`]);
    send(assistant({ type: 'thinking', thinking: TH1 } as ContentBlock));
    send(assistant({ type: 'thinking', thinking: TH2 } as ContentBlock));
    expect(shape()).toEqual([`th:${TH1}`, `th:${TH2}`]);
  });

  it('text_start opens a new text block the same way', () => {
    const { send, shape } = setup();
    send({ type: 'text_start', index: 0 } as StreamEvent, ...deltas('text_delta', 'Part one of the answer.'));
    send({ type: 'text_start', index: 1 } as StreamEvent, ...deltas('text_delta', 'Part two of the answer.'));
    expect(shape()).toEqual(['tx:Part one of the answer.', 'tx:Part two of the answer.']);
    send(assistant({ type: 'text', text: 'Part one of the answer.' } as ContentBlock));
    send(assistant({ type: 'text', text: 'Part two of the answer.' } as ContentBlock));
    expect(shape()).toEqual(['tx:Part one of the answer.', 'tx:Part two of the answer.']);
  });

  it('thinking → tool → thinking → text keeps every block in order', () => {
    const { send, shape } = setup();
    send({ type: 'thinking_start', index: 0 } as StreamEvent, ...deltas('thinking_delta', TH1));
    send(assistant({ type: 'thinking', thinking: TH1 } as ContentBlock));
    send(assistant({ type: 'tool_use', id: 'u1', name: 'Bash', summary: 'Running: ls', category: 'bash' } as ContentBlock));
    send({ type: 'tool_result', content: [{ type: 'tool_result', toolUseId: 'u1', content: 'ok', isError: false }] } as unknown as StreamEvent);
    send({ type: 'thinking_start', index: 0 } as StreamEvent, ...deltas('thinking_delta', TH2));
    send(assistant({ type: 'thinking', thinking: TH2 } as ContentBlock));
    send({ type: 'text_start', index: 1 } as StreamEvent, ...deltas('text_delta', 'Keyed by app id.'));
    send(assistant({ type: 'text', text: 'Keyed by app id.' } as ContentBlock));
    expect(shape()).toEqual([`th:${TH1}`, 'tool_use', 'tool_result', `th:${TH2}`, 'tx:Keyed by app id.']);
  });

  it('a superseded handler (reconnect/resend, same generation) writes nothing', () => {
    // 2026-10-20: the same thought rendered twice. A reconnect/resend builds a new
    // handler for the same message and generation; the old stream kept delivering.
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const { send, shape, handler2 } = setupTwo();
    send({ type: 'thinking_start', index: 0 } as StreamEvent, ...deltas('thinking_delta', TH1));
    handler2({ type: 'thinking_start', index: 0 } as StreamEvent, ...deltas('thinking_delta', TH1));
    expect(shape()).toEqual([`th:${TH1}`]);
  });
});

function setupTwo() {
  const s = setup();
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  const h2 = result.current.createStreamHandler(MSG, TAB);
  const handler2 = (...events: StreamEvent[]) => act(() => { events.forEach((e) => h2(e)); });
  // `send` drives the older handler, `handler2` the newer one for the same message.
  return { ...s, handler2 };
}

