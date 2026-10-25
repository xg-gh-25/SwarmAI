/**
 * AssistantMessageView × steer segments (chat-steer frontend §7).
 *
 * What is tested (real AssistantMessageView + turnView):
 * - `continued`: a segment a steer continued is not the turn's end: no RESULT
 *   mark, no output card, no next steps, no attention card; the hover action
 *   row stays.
 * - `afterSteer`: no avatar and no header (the reply goes on under the steer).
 * - `headerLive`: the header pulse stays while a later segment streams.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

const tu = (id: string): ContentBlock => ({ type: 'tool_use', id, name: 'Bash', input: {}, summary: 'Running: ls', category: 'bash' }) as ContentBlock;
const tr = (id: string): ContentBlock => ({ type: 'tool_result', toolUseId: id, content: 'ok', isError: false, truncated: false }) as ContentBlock;
const tx = (text: string): ContentBlock => ({ type: 'text', text }) as ContentBlock;
const deliv: ContentBlock = { type: 'turn_deliverables', files: [{ path: 'r.md', absolutePath: '/ws/r.md', kind: 'content', operation: 'written' }] } as ContentBlock;
const CONTENT = [tu('a'), tr('a'), tx('Done so far.\n\n```next-steps\nship it\n```'), deliv];

function view(props: Partial<React.ComponentProps<typeof AssistantMessageView>> = {}) {
  const message: Message = { id: 'm1', role: 'assistant', content: CONTENT, timestamp: new Date().toISOString(), isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isLastAssistant sessionId="s1" {...props} /></ToastProvider>);
}

describe('steer segments in AssistantMessageView', () => {
  it('control: an ended turn shows its next steps, output card and RESULT', () => {
    const { container } = view();
    expect(container.querySelector('[data-testid="turn-result"]')).toBeTruthy();
    expect(screen.queryByText('ship it')).toBeTruthy();
    expect(screen.queryByText('r.md')).toBeTruthy();
  });

  it('continued: no ending (next steps, output card); the action row stays', () => {
    const { container } = view({ continued: true });
    expect(screen.queryByText('ship it')).toBeNull();
    expect(screen.queryByText('r.md')).toBeNull();
    expect(screen.getByTitle('Copy message')).toBeTruthy();
    expect(container.querySelector('[data-testid="turn-result"]')).toBeNull();
  });

  it('afterSteer: no avatar, no header', () => {
    const { container } = view({ afterSteer: true });
    expect(screen.queryByText('Swarm')).toBeNull();
    expect(container.querySelector('[aria-label="Swarm"]')).toBeNull();
  });

  it('headerLive: the pulse stays on the first segment while a later one streams', () => {
    view({ continued: true, headerLive: true });
    expect(screen.getByLabelText('Streaming')).toBeTruthy();
  });
});
