/**
 * readOnly (History preview) render tests.
 *
 * The History overlay renders historical messages read-only. Interactive blocks
 * (permission / ask_user_question / escalation) gate their interactivity on
 * the pending/status signals, NOT on callback presence — so omitting callbacks
 * alone would still render live-looking controls. `readOnly` must force inert.
 *
 * Methodology: render ContentBlockRenderer directly (leaf, 0 internal callers)
 * with each interactive block + readOnly, and assert no actionable control.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { ContentBlockRenderer } from '../ContentBlockRenderer';
import type { ContentBlock, ToolResultContent } from '../../../../types';

const emptyResultMap = new Map<string, ToolResultContent>();

afterEach(() => vi.restoreAllMocks());

function permissionBlock(requestId: string): ContentBlock {
  return {
    type: 'cmd_permission_request',
    requestId,
    toolName: 'Bash',
    toolInput: { command: 'rm -rf /tmp/x' },
    reason: 'destructive command',
  } as ContentBlock;
}

function questionBlock(toolUseId: string): ContentBlock {
  return {
    type: 'ask_user_question',
    toolUseId,
    questions: [
      {
        question: 'Which approach?',
        header: 'Approach',
        multiSelect: false,
        options: [{ label: 'Option A', description: 'first' }],
      },
    ],
  } as ContentBlock;
}

describe('ContentBlockRenderer — readOnly (History preview)', () => {
  it('an UNDECIDED pending permission renders NO Approve/Deny buttons when readOnly', () => {
    const block = permissionBlock('req-1');
    render(
      <ContentBlockRenderer
        block={block}
        resultMap={emptyResultMap}
        allBlocks={[block]}
        onPermissionDecision={vi.fn()}
        pendingPermissionRequestId="req-1" // would be live-pending WITHOUT readOnly
        readOnly
      />,
    );
    // The muted shell shows "Awaiting decision", NOT the actionable buttons.
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /deny/i })).toBeNull();
    expect(screen.getByText(/awaiting decision/i)).toBeInTheDocument();
  });

  it('the SAME pending permission DOES render live buttons WITHOUT readOnly (control)', () => {
    const block = permissionBlock('req-1');
    render(
      <ContentBlockRenderer
        block={block}
        resultMap={emptyResultMap}
        allBlocks={[block]}
        onPermissionDecision={vi.fn()}
        pendingPermissionRequestId="req-1"
      />,
    );
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /deny/i })).toBeInTheDocument();
  });

  it('a pending ask_user_question is DISABLED when readOnly', () => {
    const block = questionBlock('tu-1');
    render(
      <ContentBlockRenderer
        block={block}
        resultMap={emptyResultMap}
        allBlocks={[block]}
        onAnswerQuestion={vi.fn()}
        pendingToolUseId="tu-1" // would be enabled WITHOUT readOnly
        readOnly
      />,
    );
    // Option buttons exist but are disabled (form inert).
    const optionButtons = screen.getAllByRole('button').filter((b) => /option a/i.test(b.textContent ?? ''));
    expect(optionButtons.length).toBeGreaterThan(0);
    optionButtons.forEach((b) => expect(b).toBeDisabled());
  });
});
