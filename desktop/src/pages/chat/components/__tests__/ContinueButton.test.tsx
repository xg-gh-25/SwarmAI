/**
 * Tests for the Continue button in AssistantMessageView.
 *
 * The Continue button allows users to request continuation when the model
 * issues a premature end_turn mid-response. It appears on the last
 * assistant message when not streaming and not an error.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { MessageBubble } from '../MessageBubble';
import type { Message } from '../../../../types';

// Mock dependencies that AssistantMessageView imports
vi.mock('../../../../hooks/useMemorySave', () => ({
  useMemorySave: () => ({
    statusMap: {},
    toastMap: {},
    save: vi.fn(),
    reset: vi.fn(),
  }),
}));

vi.mock('../../../../services/chat', () => ({
  chatService: { compactContext: vi.fn() },
}));

vi.mock('../../../../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

vi.mock('../../../../hooks/useSubAgentProgress', () => ({
  useSubAgentProgress: () => null,
}));

vi.mock('../../../../components/chat/SubAgentProgressBanner', () => ({
  SubAgentProgressBanner: () => null,
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
  it('renders Continue button on last assistant message when onContinue provided', () => {
    const onContinue = vi.fn();
    render(
      <MessageBubble
        message={mockAssistantMessage}
        isLastAssistant={true}
        isStreaming={false}
        onContinue={onContinue}
      />
    );

    const button = screen.getByTestId('continue-button');
    expect(button).toBeDefined();
    expect(button.textContent).toContain('Continue');
  });

  it('calls onContinue when clicked', () => {
    const onContinue = vi.fn();
    render(
      <MessageBubble
        message={mockAssistantMessage}
        isLastAssistant={true}
        isStreaming={false}
        onContinue={onContinue}
      />
    );

    const button = screen.getByTestId('continue-button');
    fireEvent.click(button);
    expect(onContinue).toHaveBeenCalledOnce();
  });

  it('does NOT render when streaming', () => {
    const onContinue = vi.fn();
    render(
      <MessageBubble
        message={mockAssistantMessage}
        isLastAssistant={true}
        isStreaming={true}
        onContinue={onContinue}
      />
    );

    expect(screen.queryByTestId('continue-button')).toBeNull();
  });

  it('does NOT render when message is an error', () => {
    const onContinue = vi.fn();
    render(
      <MessageBubble
        message={mockErrorMessage}
        isLastAssistant={true}
        isStreaming={false}
        onContinue={onContinue}
      />
    );

    expect(screen.queryByTestId('continue-button')).toBeNull();
  });

  it('does NOT render when not the last assistant message', () => {
    const onContinue = vi.fn();
    render(
      <MessageBubble
        message={mockAssistantMessage}
        isLastAssistant={false}
        isStreaming={false}
        onContinue={onContinue}
      />
    );

    expect(screen.queryByTestId('continue-button')).toBeNull();
  });

  it('does NOT render when onContinue is not provided', () => {
    render(
      <MessageBubble
        message={mockAssistantMessage}
        isLastAssistant={true}
        isStreaming={false}
      />
    );

    expect(screen.queryByTestId('continue-button')).toBeNull();
  });
});
