/**
 * The turn's ending waits for its background agents.
 *
 * A turn that dispatched background agents ends its stream before their results come
 * back. While the session still has one running, the last message's ending (Copy /
 * Compact row, next steps, output card) is not shown: the final result is not there
 * yet (XG 2026-11-01). It appears once none is running.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../../../types';

vi.mock('../../../../services/chat', () => ({ chatService: { compactSession: vi.fn() } }));
vi.mock('../../../../contexts/ToastContext', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ triggerHealthCheck: vi.fn() }),
}));

const answer: Message = {
  id: 'msg-1', role: 'assistant', timestamp: Date.now(),
  content: [{ type: 'text', text: '5 agents started.\n\n```next-steps\nCheck group A\n```' }],
};

const renderIt = (waitingOnAgents: boolean) => render(
  <MessageBubble message={answer} isLastAssistant isStreaming={false} sessionId="s1"
                 waitingOnAgents={waitingOnAgents} onContinue={vi.fn()} />);

describe('ending waits on background agents', () => {
  it('hides the action row and next steps while an agent still runs', () => {
    renderIt(true);
    expect(screen.queryByTitle('Copy message')).toBeNull();
    expect(screen.queryByTestId('next-steps')).toBeNull();
    expect(screen.getByText(/5 agents started/)).toBeDefined();   // the text itself stays
  });

  it('shows them once none runs', () => {
    renderIt(false);
    expect(screen.getByTitle('Copy message')).toBeDefined();
    expect(screen.getByTestId('next-steps')).toBeDefined();
  });
});
