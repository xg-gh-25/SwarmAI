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
// AssistantMessageView (via MessageBubble) calls useToast — stub the provider.
vi.mock('../../../../contexts/ToastContext', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn() }),
}));

import { ContentBlockRenderer } from '../ContentBlockRenderer';
import { MessageBubble } from '../MessageBubble';
import type { ContentBlock, ToolResultContent, Message } from '../../../../types';

// jsdom lacks ResizeObserver (UserMessageView / MessageBubble path uses it).
class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

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

  it('a text block renders no state-mutating control under readOnly (marker check)', () => {
    // ContentBlockRenderer text path has no buttons of its own; the Copy/Save
    // action row lives in AssistantMessageView and is gated on !readOnly there.
    // This guards the renderer-level contract: a plain text block is inert.
    const block = { type: 'text', text: 'hello', _confirmed: true } as ContentBlock;
    render(
      <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} readOnly />,
    );
    expect(screen.queryByRole('button')).toBeNull();
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

  it('MessageBubble readOnly hides the Copy action button (no side-effecting control)', () => {
    const msg: Message = {
      id: 'm1',
      role: 'assistant',
      content: [{ type: 'text', text: 'some assistant reply', _confirmed: true }],
      timestamp: new Date().toISOString(),
    } as Message;
    const { rerender } = render(<MessageBubble message={msg} readOnly />);
    expect(screen.queryByRole('button', { name: /copy/i })).toBeNull();
    // Control: without readOnly the Copy button IS rendered (in the action row).
    rerender(<MessageBubble message={msg} />);
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });
});
