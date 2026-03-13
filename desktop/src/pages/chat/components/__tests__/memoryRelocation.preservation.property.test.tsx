/**
 * Preservation Property Tests for Memory Save Button Relocation
 *
 * These tests capture baseline behavior on UNFIXED code. They MUST PASS
 * on the current code and continue to pass after the fix is applied.
 *
 * Methodology: observation-first — test what the code currently does.
 *
 * Preservation A: Copy button always present on non-streaming assistant messages
 * Preservation B: No Save-to-Memory button in non-last assistant messages
 * Preservation D: Streaming hides action buttons
 *
 * **Validates: Requirements 3.1, 3.2, 3.5, 3.6**
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import fc from 'fast-check';
import { AssistantMessageView } from '../AssistantMessageView';
import { ToastProvider } from '../../../../contexts/ToastContext';
import type { Message } from '../../../../types';

// ============== Mocks ==============

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string) => fallback,
  }),
}));

// Mock useHealth to avoid needing HealthProvider in tests
vi.mock('../../../../contexts/HealthContext', () => ({
  useHealth: () => ({
    health: { status: 'connected', lastCheckedAt: null, consecutiveFailures: 0 },
    triggerHealthCheck: vi.fn(),
  }),
}));

// ============== Helpers ==============

/**
 * Create a minimal assistant message for testing.
 */
function makeAssistantMessage(text = 'Hello from the assistant'): Message {
  return {
    id: 'msg-1',
    role: 'assistant' as const,
    content: [{ type: 'text' as const, text }],
    timestamp: new Date().toISOString(),
    isError: false,
  };
}

// ============== Arbitraries ==============

/** Arbitrary for non-empty assistant message text. */
const messageTextArb = fc.string({ minLength: 1, maxLength: 200 })
  .filter((s) => s.trim().length > 0);

// ============== Preservation Tests ==============

describe('Preservation Property Tests — Memory Save Button Relocation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Preservation A: Copy button always present on non-streaming assistant messages
   *
   * For any assistant message with isStreaming=false and non-empty text content,
   * the Copy button should be present in the DOM. The button exists in DOM but
   * is opacity-0 until hover (group-hover pattern).
   *
   * **Validates: Requirements 3.1, 3.2**
   */
  it('Preservation A: Copy button is present for non-streaming assistant messages', () => {
    fc.assert(
      fc.property(messageTextArb, (text) => {
        const msg = makeAssistantMessage(text);
        const { unmount } = render(
          <ToastProvider><AssistantMessageView message={msg} isStreaming={false} /></ToastProvider>,
        );

        // Copy button should exist in the DOM (opacity-0 until hover)
        const copyBtn = screen.getByTitle('Copy message');
        expect(copyBtn).toBeDefined();
        expect(copyBtn.textContent).toContain('Copy');

        unmount();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Preservation B: No Save-to-Memory button in non-last assistant messages
   *
   * For any assistant message rendered WITHOUT isLastAssistant=true, there
   * should be no "Save to Memory" button. On unfixed code, AssistantMessageView
   * never has a save button, so this passes trivially. After the fix, this
   * ensures non-last messages still don't show the save button.
   *
   * **Validates: Requirements 3.1**
   */
  it('Preservation B: No Save-to-Memory button on non-last assistant messages', () => {
    fc.assert(
      fc.property(messageTextArb, fc.boolean(), (text, isStreaming) => {
        const msg = makeAssistantMessage(text);
        // Render without isLastAssistant (or with isLastAssistant=false)
        const { unmount } = render(
          <ToastProvider><AssistantMessageView message={msg} isStreaming={isStreaming} /></ToastProvider>,
        );

        // No Save-to-Memory button should exist
        const saveBtn = screen.queryByTitle('Save to Memory');
        expect(saveBtn).toBeNull();

        unmount();
      }),
      { numRuns: 50 },
    );
  });

  /**
   * Preservation D: Streaming hides action buttons
   *
   * For any assistant message with isStreaming=true, no Copy button should
   * be visible. The existing code gates the copy button on !isStreaming.
   *
   * **Validates: Requirements 3.5**
   */
  it('Preservation D: Streaming hides Copy button', () => {
    fc.assert(
      fc.property(messageTextArb, (text) => {
        const msg = makeAssistantMessage(text);
        const { unmount } = render(
          <ToastProvider><AssistantMessageView message={msg} isStreaming={true} /></ToastProvider>,
        );

        // Copy button should NOT be present while streaming
        const copyBtn = screen.queryByTitle('Copy message');
        expect(copyBtn).toBeNull();

        // Also no "Copied!" variant
        const copiedBtn = screen.queryByTitle('Copied!');
        expect(copiedBtn).toBeNull();

        unmount();
      }),
      { numRuns: 50 },
    );
  });
});
