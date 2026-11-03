/**
 * A Swarm Run card that fails to load or render is its own failure: the rest of the turn
 * stays, and nothing of the panel error shows inside the chat (AssistantMessageView
 * wraps the lazy card in an ErrorBoundary whose fallback is nothing).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: () => {} },
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => String(o?.defaultValue ?? k) }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));
vi.mock('../SwarmRunCard', () => ({
  SwarmRunCard: () => { throw new Error('the card broke'); },
}));

describe('a Swarm Run card that fails', () => {
  it('leaves the rest of the turn and shows no panel error', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    const content = [
      { type: 'tool_use', id: 'b1', name: 'Bash', summary: 'Running: pipeline-v1 child dispatch', category: 'bash' },
      { type: 'tool_result', toolUseId: 'b1', content: 'ok', isError: false, truncated: false },
      { type: 'swarm_run', runId: 'run_0123456789ab', project: 'SwarmAI' },
      { type: 'text', text: 'Split into one package.' },
    ] as unknown as ContentBlock[];
    const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-11-03T13:25:00+08:00', isError: false };
    const { container } = render(<ToastProvider><AssistantMessageView message={message} isStreaming={false} isLastAssistant sessionId="s1" /></ToastProvider>);
    await waitFor(() => expect(screen.getByText('Split into one package.')).toBeTruthy());
    // the lazy card has loaded and thrown (React reports the caught error)
    await waitFor(() => expect(logged).toHaveBeenCalled());
    expect(screen.queryByTestId('swarm-run-card')).toBeNull();
    // the card's zone is there (the block was rendered) and holds nothing: no panel error
    const zone = container.querySelector('[data-zone="dispatch"]');
    expect(zone).toBeTruthy();
    expect((zone!.textContent || '').trim()).toBe('');
    // the boundary's fallback is nothing: no error text, no retry button inside the turn
    expect(document.body.textContent).not.toContain('the card broke');
    expect(screen.queryAllByRole('alert')).toHaveLength(0);
    expect(screen.queryAllByRole('button').filter((b) => /tryAgain|retry/i.test(b.textContent || ''))).toHaveLength(0);
  });
});
