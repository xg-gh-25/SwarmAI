/**
 * UserMessageView × steer (frontend design 2026-10-25-chat-steer-frontend.md §4).
 *
 * What is tested: the one line under a user bubble for each steer state, with the
 * real i18n runtime: Waiting ("Read after the current step" + a Stop link that
 * sends the Agents panel's STOP_TURN_EVENT, no Cancel), Read during / after (from
 * metadata.steered, the row's own field), Queued with a reason (question /
 * permission), and Queued with no reason keeps the label it always had.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import i18n from '../../../../i18n';
import { UserMessageView } from '../UserMessageView';
import { STOP_TURN_EVENT } from '../../turnZones';
import type { Message } from '../../../../types';

const msg = (over: Partial<Message> = {}): Message => ({
  id: 'local-s', role: 'user', timestamp: new Date().toISOString(),
  content: [{ type: 'text', text: 'skip the second one' }], metadata: { client_id: 'local-s' }, ...over,
});

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

afterEach(async () => { await act(async () => { await i18n.changeLanguage('en'); }); });

describe('steer labels under a user bubble', () => {
  it('Waiting: the line and a Stop link that stops the turn; no Cancel, no queued look', () => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener(STOP_TURN_EVENT, on);
    try {
      const { container } = render(<UserMessageView message={msg({ steerWaiting: true })} sessionId="S1" />);
      expect(screen.getByText('Read after the current step')).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stop & send now' }));
      expect(seen).toEqual([{ sessionId: 'S1' }]);
      expect(screen.queryByTitle('Cancel queued message')).toBeNull();
      expect(container.innerHTML).not.toMatch(/border-dashed/);
    } finally {
      window.removeEventListener(STOP_TURN_EVENT, on);
    }
  });

  it('Read during / Read after come from metadata.steered', () => {
    const { rerender } = render(<UserMessageView message={msg({ metadata: { client_id: 'local-s', steered: 'during' } })} />);
    expect(screen.getByText('Read during the turn')).toBeTruthy();
    rerender(<UserMessageView message={msg({ metadata: { client_id: 'local-s', steered: 'after' } })} />);
    expect(screen.getByText('Read after the reply')).toBeTruthy();
  });

  it('a read steer shows no Waiting line even if the mark lags', () => {
    render(<UserMessageView message={msg({ steerWaiting: true, metadata: { client_id: 'local-s', steered: 'during' } })} sessionId="S1" />);
    expect(screen.queryByText('Read after the current step')).toBeNull();
  });

  it('Queued: the reason names what it waits for; no reason keeps the old label', () => {
    const { rerender } = render(<UserMessageView message={msg({ isQueued: true, queuedReason: 'question' })} onCancelQueued={() => {}} />);
    expect(screen.getByText('Queued — sends after you answer the question above')).toBeTruthy();
    rerender(<UserMessageView message={msg({ isQueued: true, queuedReason: 'permission' })} onCancelQueued={() => {}} />);
    expect(screen.getByText('Queued — sends after you decide the permission above')).toBeTruthy();
    rerender(<UserMessageView message={msg({ isQueued: true, queuedReason: 'busy' })} onCancelQueued={() => {}} />);
    expect(screen.getByText('Queued — will send when ready')).toBeTruthy();
    rerender(<UserMessageView message={msg({ isQueued: true })} onCancelQueued={() => {}} />);
    expect(screen.getByText('Queued — will send when ready')).toBeTruthy();
  });

  it('zh', async () => {
    await act(async () => { await i18n.changeLanguage('zh'); });
    render(<UserMessageView message={msg({ steerWaiting: true })} sessionId="S1" />);
    expect(screen.getByText('当前这一步结束后读到')).toBeTruthy();
    expect(screen.getByRole('button', { name: '停止并立即发送' })).toBeTruthy();
  });
});
