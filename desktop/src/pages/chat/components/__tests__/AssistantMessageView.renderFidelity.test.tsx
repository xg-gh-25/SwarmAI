/**
 * OT03 — Render-fidelity guard (frontend layer of the content-loss check).
 *
 * The backend smoke (scripts/smoke_e2e.py content_shape check) verifies the
 * SERVER delivered complete assistant content. THIS test verifies the other
 * half of the OT01 content-loss class: that the DOM actually RENDERS every
 * text block a message carries — the frontend does not silently drop or
 * truncate a block at the render layer.
 *
 * WHY AssistantMessageView, not ContentBlockRenderer (adversarial Q5 fix):
 *   The OT01 bug is "complete backend response renders truncated". A text
 *   ContentBlockRenderer is a pure pass-through (block.text → MarkdownRenderer)
 *   with no drop path — testing it in isolation is tautological. The block
 *   ASSEMBLY happens in AssistantMessageView (message.content.map with its own
 *   keying, resultMap, error-wrapper branch, and the extractMessageText()>0
 *   gate at line ~256). That assembly is where a block could be dropped, so the
 *   guard must render AssistantMessageView with a real multi-block message.
 *
 * Store-level cross-turn preservation is covered by
 * preservation-cross-turn-text.test.ts; this is the VIEW-output guard.
 *
 * Methodology: render the real AssistantMessageView (heavy children mocked the
 * same way memoryRelocation.preservation does) with a multi-block assistant
 * message, assert every block's distinctive text reaches the DOM. Content-
 * STRUCTURAL assertions (block presence + full-block text), never pixel/exact-
 * AI-text — so it can't flake on output variance (method-A constraint).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message, ContentBlock } from '../../../../types';

// ── Mocks (mirror memoryRelocation.preservation.property.test.tsx) ──
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback: string) => fallback }),
}));
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

function textBlock(text: string): ContentBlock {
  return { type: 'text', text } as ContentBlock;
}

function multiBlockMessage(blocks: ContentBlock[]): Message {
  return {
    id: 'msg-fidelity-1',
    role: 'assistant' as const,
    content: blocks,
    timestamp: new Date().toISOString(),
    isError: false,
  };
}

function renderView(blocks: ContentBlock[]) {
  return render(
    <ToastProvider>
      <AssistantMessageView message={multiBlockMessage(blocks)} isStreaming={false} />
    </ToastProvider>,
  );
}

describe('AssistantMessageView — render fidelity (OT03 content-loss guard)', () => {
  it('renders EVERY text block in a multi-block message (no dropped block)', () => {
    // The agentic loop emits multiple text blocks across tool calls. ALL must
    // survive AssistantMessageView's content.map assembly — a dropped block is
    // the OT01 failure.
    renderView([
      textBlock('FIRST_BLOCK_alpha analysis of the problem'),
      textBlock('SECOND_BLOCK_bravo intermediate reasoning step'),
      textBlock('THIRD_BLOCK_charlie the final conclusion'),
    ]);
    expect(screen.getByText(/FIRST_BLOCK_alpha/)).toBeTruthy();
    expect(screen.getByText(/SECOND_BLOCK_bravo/)).toBeTruthy();
    expect(screen.getByText(/THIRD_BLOCK_charlie/)).toBeTruthy();
  });

  it('renders the FULL text of a long block (no mid-block truncation)', () => {
    const head = 'LONGBLOCK_HEAD_start';
    const tail = 'LONGBLOCK_TAIL_end';
    const filler = ' middle content that is reasonably long '.repeat(40);
    renderView([textBlock(`${head}${filler}${tail}`)]);
    // Tail rendering proves the whole block survived, not just its start.
    const node = screen.getByText(/LONGBLOCK_HEAD_start/);
    expect(node.textContent).toContain('LONGBLOCK_TAIL_end');
  });

  it('a text block AFTER a tool_use block still renders (interleaved survival)', () => {
    // OT01-class: a text block emitted after a tool result is the one most
    // likely dropped by faulty assembly. Assert it survives the mixed array.
    const blocks: ContentBlock[] = [
      textBlock('PRE_TOOL_text before the tool call'),
      { type: 'tool_use', id: 'tu-1', name: 'Read', input: {} } as ContentBlock,
      textBlock('POST_TOOL_text after the tool result'),
    ];
    renderView(blocks);
    expect(screen.getByText(/PRE_TOOL_text/)).toBeTruthy();
    expect(screen.getByText(/POST_TOOL_text/)).toBeTruthy();
  });

  it('an empty text block does not drop its sibling (graceful)', () => {
    renderView([textBlock(''), textBlock('SURVIVES_empty_sibling')]);
    expect(screen.getByText(/SURVIVES_empty_sibling/)).toBeTruthy();
  });
});
