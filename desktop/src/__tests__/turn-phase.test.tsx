/**
 * Pre-first-token turn phases (backend core/turn_phase.py) in the UI.
 *
 * What is tested:
 * - the REAL stream handler stores `turn_phase` on the tab, updates it per step,
 *   and clears it on the first other event (heartbeat / session_start keep it)
 * - a phase is never stream data: hasReceivedData stays false (it gates the
 *   connection-phase silent retry)
 * - TurnPhaseLine shows the English step label and the seconds in that step
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, render, screen } from '@testing-library/react';
import { useChatStreamingLifecycle } from '../hooks/useChatStreamingLifecycle';
import { messageStoreRegistry } from '../stores/MessageStore';
import type { StreamEvent } from '../types';
import { TurnPhaseLine } from '../pages/chat/components/TurnPhaseLine';
import { createMockDeps, initTestTab, makeMessage, resetTestState, testTabMap } from './helpers/streamingTestUtils';

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn(), toasts: [] }),
}));

const TAB = 'tab-phase';
const MSG = 'msg-phase';

function setup() {
  const { result } = renderHook(() => useChatStreamingLifecycle(createMockDeps()));
  act(() => {
    initTestTab(TAB);
    result.current.setIsStreaming(true);
    messageStoreRegistry.getOrCreate(TAB, { sessionId: 's-ph' })
      .replace([makeMessage({ id: MSG, role: 'assistant', content: [] })]);
  });
  const handler = result.current.createStreamHandler(MSG, TAB);
  const send = (e: StreamEvent) => act(() => { handler(e); });
  const tab = () => testTabMap.get(TAB)!;
  return { send, tab };
}

describe('turn_phase in the stream handler', () => {
  beforeEach(() => resetTestState());

  it('tracks each step and clears on the first token', () => {
    const { send, tab } = setup();
    send({ type: 'turn_phase', phase: 'preparing' } as StreamEvent);
    expect(tab().turnPhase?.phase).toBe('preparing');
    send({ type: 'turn_phase', phase: 'starting' } as StreamEvent);
    expect(tab().turnPhase?.phase).toBe('starting');
    send({ type: 'heartbeat' } as StreamEvent);
    send({ type: 'turn_phase', phase: 'waiting' } as StreamEvent);
    send({ type: 'session_start', sessionId: 's-ph' } as StreamEvent);
    expect(tab().turnPhase?.phase).toBe('waiting');
    expect(tab().hasReceivedData).toBeFalsy();
    send({ type: 'thinking_delta', thinking: 'first' } as StreamEvent);
    expect(tab().turnPhase).toBeUndefined();
  });

  it('ignores an unknown phase value', () => {
    const { send, tab } = setup();
    send({ type: 'turn_phase', phase: 'started' } as StreamEvent);
    expect(tab().turnPhase).toBeUndefined();
  });
});

describe('TurnPhaseLine', () => {
  it('shows the step and its seconds, no seconds in the first one', () => {
    const { rerender } = render(<TurnPhaseLine phase="starting" since={Date.now()} />);
    expect(screen.getByTestId('turn-phase').textContent).toBe('Starting session');
    rerender(<TurnPhaseLine phase="waiting" since={Date.now() - 4200} />);
    expect(screen.getByTestId('turn-phase').textContent).toBe('Waiting for model · 4s');
  });
});
