/**
 * Root 3 / 3A — AskUserQuestion surfacing render tests.
 *
 * AC1: a question with a matching pendingToolUseId renders ANSWERABLE
 *      (submit/option buttons NOT disabled) — this is the fix for the
 *      "question shows but greyed out, user can't answer" bug.
 * AC4: a malformed (empty questions) ask_user_question block logs a warning
 *      instead of vanishing silently.
 *
 * Methodology: render ContentBlockRenderer directly (leaf component, 0 internal
 * callers) with an ask_user_question block and assert button disabled state +
 * console.warn.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { ContentBlockRenderer } from '../ContentBlockRenderer';
import type { ContentBlock, ToolResultContent } from '../../../../types';

const emptyResultMap = new Map<string, ToolResultContent>();

function questionBlock(toolUseId: string): ContentBlock {
  return {
    type: 'ask_user_question',
    toolUseId,
    questions: [
      {
        question: 'Which approach?',
        header: 'Approach',
        multiSelect: false,
        options: [
          { label: 'Option A', description: 'first' },
          { label: 'Option B', description: 'second' },
        ],
      },
    ],
  } as ContentBlock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ContentBlockRenderer — ask_user_question (Root 3 / 3A)', () => {
  it('AC1: matching pendingToolUseId → option buttons are ENABLED (answerable)', () => {
    const block = questionBlock('tu-1');
    render(
      <ContentBlockRenderer
        block={block}
        resultMap={emptyResultMap}
        allBlocks={[block]}
        onAnswerQuestion={vi.fn()}
        pendingToolUseId="tu-1"   // matches → isPending → enabled
        isStreaming={false}
      />,
    );
    const optionA = screen.getByText('Option A').closest('button')!;
    expect(optionA).not.toBeNull();
    expect(optionA.disabled).toBe(false);
  });

  it('regression: NON-matching pendingToolUseId (the old bug state) → disabled', () => {
    // This is what happened before the fix: pendingToolUseId was null/undefined
    // because setPendingQuestion was gated by isActiveTab.
    const block = questionBlock('tu-1');
    render(
      <ContentBlockRenderer
        block={block}
        resultMap={emptyResultMap}
        allBlocks={[block]}
        onAnswerQuestion={vi.fn()}
        pendingToolUseId={undefined}  // the bug: no id → isAnswered → disabled
        isStreaming={false}
      />,
    );
    const optionA = screen.getByText('Option A').closest('button')!;
    expect(optionA.disabled).toBe(true);
  });

  it('AC4: empty questions block logs a warning and renders nothing', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const badBlock = { type: 'ask_user_question', toolUseId: 'tu-bad', questions: [] } as unknown as ContentBlock;
    const { container } = render(
      <ContentBlockRenderer
        block={badBlock}
        resultMap={emptyResultMap}
        allBlocks={[badBlock]}
        onAnswerQuestion={vi.fn()}
        pendingToolUseId="tu-bad"
        isStreaming={false}
      />,
    );
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining('ask_user_question block dropped'),
      expect.objectContaining({ toolUseId: 'tu-bad' }),
    );
    // Nothing user-facing rendered for the malformed block
    expect(container.querySelector('button')).toBeNull();
  });
});
