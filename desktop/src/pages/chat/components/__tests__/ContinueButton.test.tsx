/**
 * Tests for the Continue button.
 *
 * Continue asks the agent to go on after a turn stopped early, so it is on the last
 * message's error card (an interrupted or failed turn), never under a normal answer;
 * a card that has its own fix (sign-in, retry, ...) gets none.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../../../types';

vi.mock('../../../../services/chat', () => ({
  chatService: { compactContext: vi.fn() },
}));

vi.mock('../../../../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

// An errored message renders its local ChatErrorMessage, which reads HealthContext.
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ triggerHealthCheck: vi.fn() }),
}));

const mockAssistantMessage: Message = {
  id: 'msg-1',
  role: 'assistant',
  content: [{ type: 'text', text: 'Here is my partial response about...' }],
  timestamp: Date.now(),
};

const mockErrorMessage: Message = {
  ...mockAssistantMessage,
  id: 'msg-err',
  isError: true,
};

describe('Continue Button', () => {
  it('is not under a normal answer, even on the last message', () => {
    render(<MessageBubble message={mockAssistantMessage} isLastAssistant isStreaming={false}
                          onContinue={vi.fn()} />);
    expect(screen.queryByTestId('continue-button')).toBeNull();
  });

  it('is on the last message\'s interrupted card and sends continue', () => {
    const onContinue = vi.fn();
    render(<MessageBubble message={mockErrorMessage} isLastAssistant isStreaming={false}
                          onContinue={onContinue} />);
    const button = screen.getByTestId('continue-button');
    expect(button.textContent).toContain('Continue');
    fireEvent.click(button);
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it('is on a timeout card too', () => {
    render(<MessageBubble message={{ ...mockErrorMessage, errorCode: 'AGENT_TIMEOUT' }}
                          isLastAssistant isStreaming={false} onContinue={vi.fn()} />);
    expect(screen.getByTestId('continue-button')).toBeDefined();
  });

  it('is not on a card that has its own fix', () => {
    for (const code of ['CREDENTIALS_EXPIRED', 'ACCOUNT_NOT_CONNECTED', 'QUEUE_TIMEOUT',
                        'RATE_LIMIT_EXCEEDED', 'SERVICE_UNAVAILABLE']) {
      const { unmount } = render(
        <MessageBubble message={{ ...mockErrorMessage, errorCode: code }} isLastAssistant
                       isStreaming={false} onContinue={vi.fn()} />);
      expect(screen.queryByTestId('continue-button'), code).toBeNull();
      unmount();
    }
  });

  it('is not on an older message\'s error card', () => {
    render(<MessageBubble message={mockErrorMessage} isLastAssistant={false} isStreaming={false}
                          onContinue={vi.fn()} />);
    expect(screen.queryByTestId('continue-button')).toBeNull();
  });

  it('is not shown without onContinue', () => {
    render(<MessageBubble message={mockErrorMessage} isLastAssistant isStreaming={false} />);
    expect(screen.queryByTestId('continue-button')).toBeNull();
  });
});

describe('Save to Memory', () => {
  it('is gone from the action row', () => {
    render(<MessageBubble message={mockAssistantMessage} isLastAssistant isStreaming={false}
                          sessionId="s1" onContinue={vi.fn()} />);
    expect(screen.queryByTitle('Save to Memory')).toBeNull();
    expect(screen.queryByText('Save')).toBeNull();
  });
});
