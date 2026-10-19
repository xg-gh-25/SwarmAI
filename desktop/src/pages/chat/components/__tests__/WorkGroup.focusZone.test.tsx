/**
 * The focus zone (chat display v8, XG 2026-10-19 — "肉眼欺骗"): what is streaming is
 * shown in full; when the next action starts, it stays as it was for FOLD_DELAY_MS
 * and only then folds to one line. Nothing shown disappears on the spot — inside a
 * pipeline stage line too.
 *
 * What is tested (real AssistantMessageView, a pipeline turn mid-BUILD):
 * - a thought, then a tool call: at that moment the thought is still in full; after
 *   FOLD_DELAY_MS it is one "Thought" line
 * - a finished call, then another: both rows are still rows; after FOLD_DELAY_MS
 *   exploration rows have merged into one Explored row
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import { FOLD_DELAY_MS } from '../../../../hooks/useLinger';
import type { Message, ContentBlock } from '../../../../types';
import { runBlock, row, tu, tr, th, stageRun } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

afterEach(() => { vi.useRealTimers(); });

const view = (content: ContentBlock[]) => {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:59:00+08:00', isError: false };
  return <ToastProvider><AssistantMessageView message={message} isStreaming isLastAssistant /></ToastProvider>;
};
const build = () => screen.getAllByTestId('checklist-line').find((l) => l.dataset.stage === 'build')!;
const HEAD = [runBlock(), ...stageRun('evaluate', 1), ...stageRun('think', 1), ...stageRun('plan', 1), row('build', 'in_progress')];
const THOUGHT = 'I should check the reconcile path before touching the store, since the tab switch replays it.';

describe('focus zone inside a pipeline stage', () => {
  it('a thought stays in full when the next call arrives, and folds FOLD_DELAY_MS later', () => {
    vi.useFakeTimers();
    const { rerender } = render(view([...HEAD, tu('b1', 'make'), tr('b1'), th(THOUGHT)]));
    expect(build().textContent).toContain(THOUGHT);
    rerender(view([...HEAD, tu('b1', 'make'), tr('b1'), th(THOUGHT), tu('b2', 'make test')]));
    expect(build().textContent).toContain(THOUGHT); // not on the spot
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(build().textContent).not.toContain(THOUGHT);
    expect(build().textContent).toContain('Thought');
  });

  it('finished exploration rows stay rows until they settle, then merge into one Explored row', () => {
    vi.useFakeTimers();
    const { rerender } = render(view([...HEAD, tu('b1', 'sed -n 1,9p src/a.ts'), tr('b1')]));
    rerender(view([...HEAD, tu('b1', 'sed -n 1,9p src/a.ts'), tr('b1'), tu('b2', 'cat src/b.ts'), tr('b2'), tu('b3', 'make')]));
    const rowsNow = build().querySelectorAll('[data-testid="step-row"]').length;
    expect(rowsNow).toBe(3);
    act(() => { vi.advanceTimersByTime(FOLD_DELAY_MS + 50); });
    expect(build().querySelectorAll('[data-testid="step-explored"]').length).toBe(1);
  });
});
