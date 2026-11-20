/**
 * Streamed text that arrives after the user's Stop (useChatStreamingLifecycle's
 * stream handler, the `_stoppedHandler` guard; stopTurn sets it).
 *
 * Question: does a stopped turn take more text after its Stopped marker? The backend
 * streams on until its stop lands, and the window's subscription delivers it to the
 * turn's handler; appended to the marker it read "Stoppedword". And does a turn begun
 * after the Stop (its own handler) still stream?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import type { ContentBlock, StreamEvent } from '../types';
import { createMockDeps, initTestTab, makeMessage, resetTestState, testTabMap } from './helpers/streamingTestUtils';

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

const TAB = 'tab-stop';
const MARKER = '\n\n---\n*Stopped*';
const texts = (id: string) => messageStoreRegistry.getOrCreate(TAB).getSnapshot().find((m) => m.id === id)!.content
  .filter((b) => b.type === 'text').map((b) => (b as { text: string }).text);

describe('stream handler after a Stop', () => {
  beforeEach(() => resetTestState());

  it('the stopped turn takes no more text; a later turn streams', () => {
    const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
    act(() => {
      initTestTab(TAB);
      result.current.setLocalSending(true);
      messageStoreRegistry.getOrCreate(TAB, { sessionId: 's-stop' })
        .replace([makeMessage({ id: 'm1', role: 'assistant', content: [] })]);
    });
    const h1 = result.current.createStreamHandler('m1', TAB);
    act(() => { h1({ type: 'text_start', index: 0 } as StreamEvent); h1({ type: 'text_delta', text: 'word word ' } as StreamEvent); });
    act(() => {   // what stopTurn does to the tab and its store
      const tab = testTabMap.get(TAB)!;
      tab.userStopped = true;
      tab._stoppedHandler = tab.latestStreamHandler;
      messageStoreRegistry.getOrCreate(TAB).endStreaming();
      messageStoreRegistry.getOrCreate(TAB).updateLast(
        (m) => ({ ...m, content: [...m.content, { type: 'text', text: MARKER, _confirmed: true } as ContentBlock] }),
        (m) => m.role === 'assistant');
    });
    act(() => {
      h1({ type: 'text_delta', text: 'word' } as StreamEvent);
      h1({ type: 'text_start', index: 1 } as StreamEvent);
      h1({ type: 'text_delta', text: 'more' } as StreamEvent);
      h1({ type: 'thinking_delta', thinking: 'late thought' } as StreamEvent);
    });
    // the confirmation of the paragraph Stop cut off adds nothing either, and never
    // takes the marker's place
    act(() => {
      h1({ type: 'assistant', content: [{ type: 'text', text: 'word word word word, the rest of it.' }] } as StreamEvent);
    });
    expect(texts('m1')).toEqual(['word word ', MARKER]);
    // a turn begun after the Stop (a CLI-started turn, a new send) has its own handler
    act(() => {
      messageStoreRegistry.getOrCreate(TAB).replace([
        ...messageStoreRegistry.getOrCreate(TAB).getSnapshot(),
        makeMessage({ id: 'm2', role: 'assistant', content: [] }),
      ]);
    });
    const h2 = result.current.createStreamHandler('m2', TAB);
    act(() => { h2({ type: 'text_start', index: 0 } as StreamEvent); h2({ type: 'text_delta', text: 'Next answer.' } as StreamEvent); });
    expect(texts('m2')).toEqual(['Next answer.']);
  });
});
