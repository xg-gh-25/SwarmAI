/**
 * A turn that ended in an error shows the same error card live and after a reload
 * (AssistantMessageView: message.isError live, view.failure from the persisted
 * turn_outcome after a reload).
 *
 * Questions: does a reloaded failed turn show the card (message, suggested action,
 * Continue) once, with no plain error line and no next-step chips? Does a live failed
 * turn still show exactly one card? Does an outcome saved before the suggested action
 * was kept still give the card?
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

const healthCheck = vi.hoisted(() => vi.fn());
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: healthCheck,
  }),
}));

const MSG = 'API Error: 400 The scripted model refused this request.';
const TIP = 'Your conversation is saved. Send your message again to continue.';
const outcome = (extra: Record<string, unknown> = {}) =>
  ({ type: 'turn_outcome', ended: 'error', code: 'MODEL_ERROR', message: MSG, ...extra } as unknown as ContentBlock);

function renderMessage(message: Partial<Message>) {
  const full: Message = { id: 'm1', role: 'assistant', content: [], timestamp: new Date().toISOString(), isError: false, ...message };
  return render(<ToastProvider>
    <AssistantMessageView message={full} isLastAssistant onContinue={() => {}} />
  </ToastProvider>);
}

describe('the error card, live and reloaded', () => {
  it('a reloaded failed turn shows the error card, once, without next steps', () => {
    renderMessage({ content: [
      { type: 'text', text: 'Working on it.\n\n```next-steps\nTry again\n```' } as ContentBlock,
      outcome({ suggested_action: TIP }),
    ] });
    expect(screen.getAllByText(MSG)).toHaveLength(1);
    expect(screen.queryByText(`⚠️ ${MSG}`)).toBeNull();
    expect(screen.getByText(new RegExp(TIP))).toBeTruthy();
    expect(screen.getByTestId('continue-button')).toBeTruthy();
    expect(screen.queryByText('Try again')).toBeNull();
  });

  it('a live failed turn shows one card', () => {
    renderMessage({
      isError: true, errorCode: 'MODEL_ERROR', suggestedAction: TIP,
      content: [outcome({ suggested_action: TIP }),
        { type: 'text', text: `⚠️ ${MSG}`, _liveError: true } as ContentBlock],
    });
    expect(screen.getAllByText(new RegExp(MSG.replace(/[.]/g, '\\.')))).toHaveLength(1);
    expect(screen.getAllByTestId('continue-button')).toHaveLength(1);
  });

  it('an older outcome without a suggested action still shows the card', () => {
    renderMessage({ content: [outcome()] });
    expect(screen.getAllByText(MSG)).toHaveLength(1);
    expect(screen.getByTestId('continue-button')).toBeTruthy();
  });

  it('a reloaded SERVICE_UNAVAILABLE card starts no health check; a live one does', () => {
    healthCheck.mockClear();
    renderMessage({ content: [outcome({ code: 'SERVICE_UNAVAILABLE' })] });
    expect(healthCheck).not.toHaveBeenCalled();
    renderMessage({ isError: true, errorCode: 'SERVICE_UNAVAILABLE',
      content: [{ type: 'text', text: 'down', _liveError: true } as ContentBlock] });
    expect(healthCheck).toHaveBeenCalledTimes(1);
  });
});
