/**
 * Render cost while streaming: a new text token must not re-render the tool rows
 * that already finished. Counted through usePendingToolElapsed, which every
 * ToolStripV6 render calls exactly once. test-setup imports TurnZones early, so
 * the hook is mocked after vi.resetModules() and the view imported dynamically.
 */
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import type { ContentBlock, Message } from '../../../../types';

const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const bash = (id: string, cmd: string): ContentBlock =>
  ({ type: 'tool_use', id, name: 'Bash', summary: `Running: ${cmd}`, category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;

describe('streaming render cost', () => {
  it('a streamed token re-renders no finished tool row', async () => {
    vi.resetModules();
    const counter = { n: 0 };
    vi.doMock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: unknown) => f, i18n: { language: 'en' } }) }));
    vi.doMock('../../../../contexts/HealthContext', () => ({
      useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
    }));
    vi.doMock('../../../../hooks/usePendingToolElapsed', async (orig) => {
      const m = await orig<typeof import('../../../../hooks/usePendingToolElapsed')>();
      return { ...m, usePendingToolElapsed: (...a: Parameters<typeof m.usePendingToolElapsed>) => { counter.n += 1; return m.usePendingToolElapsed(...a); } };
    });
    const { AssistantMessageView } = await import('../AssistantMessageView');
    const { ToastProvider } = await import('../../../../contexts/ToastContext');
    const view = (content: ContentBlock[]) => {
      const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-22T10:00:00+08:00', isError: false };
      return <ToastProvider><AssistantMessageView message={message} isStreaming isLastAssistant sessionId="s1" /></ToastProvider>;
    };

    const calls: ContentBlock[] = [];
    // reads and commands alternate, so no two adjacent calls merge: 80 rows (spec §3.1)
    for (let i = 0; i < 80; i++) calls.push(bash(`b${i}`, i % 2 ? `make thing${i}` : `grep -n thing${i} src`), tr(`b${i}`));
    const base = [tx('Looking.'), ...calls];
    let text = 'Now the answer is';
    const r = render(view([...base, tx(text)]));
    expect(counter.n).toBeGreaterThanOrEqual(80); // the rows did render
    counter.n = 0;
    for (let i = 0; i < 10; i++) {
      text += ` w${i}`;
      r.rerender(view([...base, tx(text)]));
    }
    expect(counter.n).toBe(0);
    // a call finishing still re-renders its row
    const live = [...base, bash('z', 'make'), tx(text)];
    r.rerender(view(live));
    counter.n = 0;
    r.rerender(view([...base, bash('z', 'make'), tr('z'), tx(text)]));
    expect(counter.n).toBeGreaterThan(0);
    r.unmount();
    vi.resetModules();
  });
});
