/**
 * AssistantMessageView × StreamingBee — the bee replaces the old pulsing caret.
 *
 * What is tested: a streaming message renders the bee (flying) and no caret
 * span; isResting switches it to the resting variant with the "{name} 在琢磨…"
 * line under the REAL zh i18n runtime; the resting line appears only when
 * resting; a finished message renders neither bee nor caret.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import i18n from '../../../../i18n';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { ASSISTANT_NAME } from '../../assistantName';
import type { Message } from '../../../../types';

vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const message: Message = {
  id: 'm1', role: 'assistant', timestamp: new Date().toISOString(), isError: false,
  content: [{ type: 'text', text: 'Working on it' }],
};

function renderView(props: { isStreaming?: boolean; isResting?: boolean }) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={message} isLastAssistant sessionId="s1" {...props} />
    </ToastProvider>,
  );
}

afterEach(async () => {
  cleanup();
  await act(async () => { await i18n.changeLanguage('en'); });
});

describe('streaming indicator', () => {
  it('streaming: the bee is shown (flying) and the old caret is gone', () => {
    const { container } = renderView({ isStreaming: true });
    const bee = screen.getByTestId('streaming-bee');
    expect(bee.className).toContain('streaming-bee--flying');
    expect(container.querySelector('span.animate-pulse.w-2.h-4')).toBeNull();
    // Live region mounted but silent until resting.
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('streaming + resting: zh line "{name} 在琢磨…" appears', async () => {
    await act(async () => { await i18n.changeLanguage('zh'); });
    renderView({ isStreaming: true, isResting: true });
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--resting');
    expect(screen.getByRole('status').textContent).toBe(`${ASSISTANT_NAME} 在琢磨…`);
  });

  it('not streaming: neither bee nor caret, even if isResting leaks through', () => {
    const { container } = renderView({ isStreaming: false, isResting: true });
    expect(screen.queryByTestId('streaming-bee')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(container.querySelector('span.animate-pulse.w-2.h-4')).toBeNull();
  });
});
