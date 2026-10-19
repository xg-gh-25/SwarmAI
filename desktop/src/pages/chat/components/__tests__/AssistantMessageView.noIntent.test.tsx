/**
 * No "Intent" label anywhere (chat display v8, XG 2026-10-19).
 *
 * What is tested (real AssistantMessageView): the opening restatement is plain text
 * at the top of the turn, in a chat turn AND in a pipeline turn; the run's
 * requirement is never shown; a turn with no restatement shows no lead item (its
 * first thought stays inside the work); none of the three former label sites
 * (run alignment card, alignment text, intent thought) renders "Intent".
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';
import { runBlock, row, tu, tr, tx, th } from './fixtures/pipelineBlocks';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: unknown) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({ health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 }, triggerHealthCheck: vi.fn() }),
}));

function renderView(content: ContentBlock[], isStreaming = false) {
  const message: Message = { id: 'm1', role: 'assistant', content, timestamp: '2026-10-19T09:59:00+08:00', isError: false };
  return render(<ToastProvider><AssistantMessageView message={message} isStreaming={isStreaming} isLastAssistant /></ToastProvider>);
}
const noIntentLabel = () => expect(screen.queryByText(/^Intent$/i)).toBeNull();

describe('no Intent label', () => {
  it('pipeline turn: the restatement is plain text above the block; the requirement is not shown', () => {
    renderView([
      tx('RESTATEMENT of the ask'), runBlock(),
      row('evaluate', 'in_progress'), tu('a'), tr('a'), row('evaluate', 'completed'),
      tx('the answer'),
    ]);
    noIntentLabel();
    expect(screen.queryByText(/REQUIREMENT TEXT/)).toBeNull();
    const lead = screen.getByText('RESTATEMENT of the ask');
    const block = screen.getByTestId('pipeline-block');
    expect(block.contains(lead)).toBe(false);
    // the restatement comes before the block
    expect(lead.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('pipeline turn: a restatement written before the pipeline_run block is not a lone WORKED block', () => {
    renderView([tx('RESTATEMENT first'), runBlock(), row('evaluate', 'in_progress'), tu('a'), tr('a')], true);
    const lead = screen.getByText('RESTATEMENT first');
    expect(screen.queryAllByTestId('work-group').some((g) => g.contains(lead))).toBe(false);
    expect(screen.getByTestId('pipeline-block').contains(lead)).toBe(false);
  });

  it('chat turn with a restatement: plain text, no label', () => {
    renderView([tx('RESTATEMENT here'), tu('a'), tr('a'), tx('the answer')]);
    noIntentLabel();
    expect(screen.getByText('RESTATEMENT here')).toBeTruthy();
  });

  it('chat turn without a restatement: no lead item, the opening thought stays in the work', () => {
    renderView([th('an opening thought that is long enough to have been picked as the intent before this change'), tu('a'), tr('a'), tx('the answer')]);
    noIntentLabel();
    expect(screen.queryAllByTestId('thought').every((t) => t.dataset.intent !== 'true')).toBe(true);
    expect(screen.queryByTestId('turn-restatement')).toBeNull();
  });
});
