/**
 * Regression tests for the TSCC "View Full Prompt" modal (FullPromptModal).
 *
 * Bug (run_4ddaee2c): the modal used `position: fixed inset-0` but rendered as a
 * DOM descendant of the `.animate-tscc-panel` popover, whose forwards-filled
 * animation retains a non-none transform (`scale(1)`). A non-none transform on an
 * ancestor makes IT the containing block for `fixed` descendants — so the modal
 * was trapped inside the ~720px popover box instead of covering the viewport, and
 * dismissing/backing out felt broken.
 *
 * Fix: render FullPromptModal via createPortal(document.body) so its `fixed inset-0`
 * resolves against the viewport. Because the popover has a document-level mousedown
 * "outside-close" listener, the portaled modal must stopPropagation on mousedown so
 * clicking the modal doesn't also close the underlying popover, and it owns a
 * capture-phase Escape handler that closes the modal only.
 *
 * Properties asserted:
 * - AC1: the modal's dialog element is a direct child of document.body (portaled out).
 * - AC2: a mousedown on the modal does NOT reach a document-level listener (isolation).
 * - AC4: Escape while the modal is open calls onClose (modal-scoped dismissal).
 *
 * Testing methodology: React Testing Library, tscc service mocked, drive the real
 * PromptTab → View Full Prompt → modal path via SystemPromptModule.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { SystemPromptModule } from '../TSCCModules';
import type { SystemPromptMetadata } from '../../../../types';

vi.mock('../../../../services/tscc', () => ({
  getSystemPromptMetadata: vi.fn(),
  getRecallSnapshot: vi.fn(),
  getSecurityScan: vi.fn(),
}));

import {
  getSystemPromptMetadata,
  getRecallSnapshot,
  getSecurityScan,
} from '../../../../services/tscc';

const metadata: SystemPromptMetadata = {
  files: [{ filename: 'SWARMAI.md', tokens: 500, truncated: false }],
  totalTokens: 500,
  effectiveTokenBudget: 100000,
  fullText: 'THE FULL SYSTEM PROMPT TEXT',
};

async function openFullPromptModal() {
  render(<SystemPromptModule sessionId="s1" metadata={metadata} />);
  // Go to the Prompt tab
  fireEvent.click(screen.getByRole('button', { name: /Prompt/i }));
  // Click "View Full Prompt" (metadata.fullText present → opens synchronously)
  fireEvent.click(screen.getByRole('button', { name: /View Full Prompt/i }));
  await waitFor(() =>
    expect(screen.getByRole('dialog', { name: /Full system prompt/i })).toBeTruthy()
  );
  return screen.getByRole('dialog', { name: /Full system prompt/i });
}

describe('FullPromptModal', () => {
  beforeEach(() => {
    vi.mocked(getSystemPromptMetadata).mockResolvedValue(metadata);
    vi.mocked(getRecallSnapshot).mockResolvedValue(null);
    vi.mocked(getSecurityScan).mockResolvedValue(null);
  });

  it('AC1: renders as a direct child of document.body (portaled to escape the transformed popover)', async () => {
    const dialog = await openFullPromptModal();
    // The portaled modal's outermost node is a direct child of <body>.
    expect(dialog.parentElement).toBe(document.body);
    expect(dialog.textContent).toContain('THE FULL SYSTEM PROMPT TEXT');
  });

  it('AC2: a mousedown on the modal does NOT propagate to a document-level listener', async () => {
    const dialog = await openFullPromptModal();
    const docMouseDown = vi.fn();
    document.addEventListener('mousedown', docMouseDown);
    try {
      fireEvent.mouseDown(dialog);
      // The modal stops propagation so the popover's document mousedown never fires.
      expect(docMouseDown).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('mousedown', docMouseDown);
    }
  });

  it('AC4: Escape closes the modal (and stops the event so the popover stays open)', async () => {
    const dialog = await openFullPromptModal();
    const docKeyDown = vi.fn();
    // Register on the NON-capture (bubble) phase — the modal's capture-phase
    // handler runs first and stops propagation, so this must never see Escape.
    document.addEventListener('keydown', docKeyDown);
    try {
      fireEvent.keyDown(dialog, { key: 'Escape' });
      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: /Full system prompt/i })).toBeNull()
      );
      expect(docKeyDown).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', docKeyDown);
    }
  });
});
