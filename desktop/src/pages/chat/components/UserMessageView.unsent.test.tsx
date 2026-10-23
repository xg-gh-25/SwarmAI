/**
 * UserMessageView "Not sent · Retry" (docs/design/chat-turn-liveness.md S3, T7c).
 *
 * A user message the backend never accepted (sendState 'failed') shows "Not sent"
 * and a Retry button that calls onRetrySend; an accepted one shows neither.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { UserMessageView } from './UserMessageView';
import type { Message } from '../../../types';

// jsdom lacks ResizeObserver (the bubble's overflow check uses it)
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

const msg: Message = {
  id: 'local-r', role: 'user', content: [{ type: 'text', text: 'hi' }],
  timestamp: new Date().toISOString(),
};

describe('UserMessageView — a send the backend never accepted', () => {
  it('shows "Not sent" and a Retry that calls onRetrySend', () => {
    const onRetry = vi.fn();
    render(<UserMessageView message={{ ...msg, sendState: 'failed' }} onRetrySend={onRetry} />);
    expect(screen.getByText('Not sent')).toBeTruthy();
    fireEvent.click(screen.getByText('Retry'));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('shows neither once the send is accepted', () => {
    render(<UserMessageView message={msg} onRetrySend={vi.fn()} />);
    expect(screen.queryByText('Not sent')).toBeNull();
    expect(screen.queryByText('Retry')).toBeNull();
  });
});
