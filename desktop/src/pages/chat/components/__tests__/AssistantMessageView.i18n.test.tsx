/**
 * AssistantMessageView action row × the REAL i18n runtime (no react-i18next mock).
 *
 * What is tested: under zh the Copy / Save / Continue buttons, their titles and
 * aria-labels are Chinese (they were hardcoded English); under en the text is
 * exactly what it was before, so existing label/title queries keep working; the
 * Continue title names the assistant from ASSISTANT_NAME.
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
const renderRow = () => render(
  <ToastProvider>
    <AssistantMessageView message={message} isLastAssistant sessionId="s1" onContinue={() => {}} />
  </ToastProvider>,
);

afterEach(async () => { await act(async () => { await i18n.changeLanguage('en'); }); });

describe('message action row — localized', () => {
  it('zh: buttons, titles and aria-labels are Chinese', async () => {
    await act(async () => { await i18n.changeLanguage('zh'); });
    renderRow();
    expect(screen.getByTitle('复制这条回复').textContent).toContain('复制');
    expect(screen.getByLabelText('存入记忆').textContent).toContain('保存');
    const cont = screen.getByTestId('continue-button');
    expect(cont.getAttribute('aria-label')).toBe('继续回复');
    expect(cont.getAttribute('title')).toBe('继续：让 Swarm 接着回答');
    expect(cont.textContent).toContain('继续');
  });

  it('en: the text is what it always was', () => {
    renderRow();
    expect(screen.getByTitle('Copy message').textContent).toContain('Copy');
    expect(screen.getByLabelText('Save to Memory').textContent).toContain('Save');
    expect(screen.getByTestId('continue-button').getAttribute('aria-label')).toBe('Continue response');
  });
});
