/**
 * AssistantMessageView × StreamingBee — the bee replaces the old pulsing caret.
 *
 * What is tested: a streaming message renders the bee (flying) and no caret
 * span; after 45s with no new content the zh wait hint appears under the REAL zh
 * i18n runtime while the bee keeps flying; a finished message renders neither.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act, cleanup } from '@testing-library/react';
import i18n from '../../../../i18n';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { WAIT_HINT_MS } from '../../../../hooks/useStreamingWait';
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

function renderView(props: { isStreaming?: boolean }) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={message} isLastAssistant sessionId="s1" {...props} />
    </ToastProvider>,
  );
}

afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  await act(async () => { await i18n.changeLanguage('en'); });
});

describe('streaming indicator', () => {
  it('streaming: the bee is shown (flying) and the old caret is gone', () => {
    const { container } = renderView({ isStreaming: true });
    const bee = screen.getByTestId('streaming-bee');
    expect(bee.className).toContain('streaming-bee--flying');
    expect(container.querySelector('span.animate-pulse.w-2.h-4')).toBeNull();
    // Live region mounted but silent until the 45s hint.
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('streaming, 45s quiet: the zh hint appears and the bee keeps flying', async () => {
    await act(async () => { await i18n.changeLanguage('zh'); });
    vi.useFakeTimers();
    renderView({ isStreaming: true });
    act(() => { vi.advanceTimersByTime(WAIT_HINT_MS); });
    expect(screen.getByTestId('streaming-bee').className).toContain('streaming-bee--flying');
    expect(document.querySelector('.streaming-bee-note-text')?.textContent).toBe('等待模型回应 · 45 秒');
    expect(screen.getByRole('status').textContent).toBe('等待模型回应');
  });

  it('not streaming: neither bee nor caret', () => {
    const { container } = renderView({ isStreaming: false });
    expect(screen.queryByTestId('streaming-bee')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
    expect(container.querySelector('span.animate-pulse.w-2.h-4')).toBeNull();
  });
});
