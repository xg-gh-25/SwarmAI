/**
 * AssistantMessageView action row × the REAL i18n runtime (no react-i18next mock).
 *
 * What is tested: under zh the Copy button (answer) and the Continue button (error
 * card of the last message), their titles and aria-labels are Chinese; under en the
 * text is what it was before; the Continue title names the assistant from
 * ASSISTANT_NAME.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import i18n from '../../../../i18n';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message } from '../../../../types';

vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const message: Message = {
  id: 'm1', role: 'assistant', timestamp: new Date().toISOString(), isError: false,
  content: [{ type: 'text', text: 'Done.' }],
};
const interrupted: Message = { ...message, id: 'm2', isError: true, content: [] };
const renderRow = () => render(
  <ToastProvider>
    <AssistantMessageView message={message} isLastAssistant={false} sessionId="s1" />
    <AssistantMessageView message={interrupted} isLastAssistant sessionId="s1" onContinue={() => {}} />
  </ToastProvider>,
);

afterEach(async () => { await act(async () => { await i18n.changeLanguage('en'); }); });

describe('message action row — localized', () => {
  it('zh: buttons, titles and aria-labels are Chinese', async () => {
    await act(async () => { await i18n.changeLanguage('zh'); });
    renderRow();
    expect(screen.getByTitle('复制这条回复').textContent).toContain('复制');
    const cont = screen.getByTestId('continue-button');
    expect(cont.getAttribute('aria-label')).toBe('继续回复');
    expect(cont.getAttribute('title')).toBe('继续：让 Swarm 接着回答');
    expect(cont.textContent).toContain('继续');
  });

  it('en: the text is what it always was', () => {
    renderRow();
    expect(screen.getByTitle('Copy message').textContent).toContain('Copy');
    expect(screen.getByTestId('continue-button').getAttribute('aria-label')).toBe('Continue response');
  });
});
