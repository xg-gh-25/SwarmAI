/**
 * Bug Condition Exploration Property Tests — useMemorySave & AssistantMessageView
 *
 * Testing methodology: Property-based (fast-check) + @testing-library/react
 * What is being tested:
 *   1. Save-to-Memory button placement (should be in AssistantMessageView, not ChatHeader)
 *   2. Per-session status isolation in useMemorySave hook
 *
 * Key properties being verified:
 *   - Property 1 (Button Location): AssistantMessageView with isLastAssistant=true renders
 *     a "Save to Memory" button. On UNFIXED code this FAILS because the button lives in ChatHeader.
 *   - Property 2 (Status Isolation): useMemorySave provides per-session statusMap so that
 *     saving session S1 does NOT affect the status of session S2. On UNFIXED code this FAILS
 *     because the hook returns a single global `status`, not a per-session `statusMap`.
 *
 * **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2**
 *
 * CRITICAL: These tests are EXPECTED TO FAIL on unfixed code. Failure confirms the bug exists.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { renderHook } from '@testing-library/react';
import fc from 'fast-check';

// ============== Mocks ==============

// Mock react-i18next (matching existing test patterns)
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string) => fallback,
  }),
}));

// Mock the API service — return a successful SaveSessionResponse
vi.mock('../../services/api', () => ({
  default: {
    post: vi.fn().mockResolvedValue({
      data: {
        status: 'saved',
        entries: {
          key_decisions: 1,
          lessons_learned: 1,
          open_threads: 0,
          recent_context: 1,
        },
        total_saved: 3,
        next_message_idx: 5,
        message: null,
      },
    }),
  },
}));

// ============== Imports (after mocks) ==============

import { AssistantMessageView } from '../../pages/chat/components/AssistantMessageView';
import { useMemorySave } from '../useMemorySave';

// ============== Arbitraries ==============

/** Generate non-empty alphanumeric session ID strings for property tests. */
const sessionIdArb = fc.stringMatching(/^[a-zA-Z0-9]{1,20}$/);

/** Generate pairs of distinct session IDs. */
const distinctSessionPairArb = fc
  .tuple(sessionIdArb, sessionIdArb)
  .filter(([a, b]) => a !== b);

// ============== Test Helpers ==============

/** Minimal assistant message fixture for rendering AssistantMessageView. */
function makeAssistantMessage(text = 'Hello from the assistant') {
  return {
    id: 'msg-1',
    role: 'assistant' as const,
    content: [{ type: 'text' as const, text }],
    timestamp: new Date().toISOString(),
    isError: false,
  };
}

// ============== Property Tests ==============

describe('Bug Condition Exploration: Save Button & Status Isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Property 1 (Button Location): AssistantMessageView with isLastAssistant=true,
   * isStreaming=false, and a sessionId should render a "Save to Memory" button.
   *
   * On UNFIXED code this FAILS because AssistantMessageView does not have a
   * Save-to-Memory button — it currently lives in ChatHeader.
   *
   * **Validates: Requirements 2.1**
   */
  it('should render a Save-to-Memory button in AssistantMessageView for the last assistant message', () => {
    fc.assert(
      fc.property(sessionIdArb, (sessionId) => {
        const message = makeAssistantMessage();

        // Render with the post-fix props: isLastAssistant and sessionId
        // On unfixed code these extra props are simply ignored by React
        const { unmount } = render(
          <AssistantMessageView
            message={message}
            isStreaming={false}
            {...({ isLastAssistant: true, sessionId } as Record<string, unknown>)}
          />
        );

        // The Save-to-Memory button should exist in AssistantMessageView
        const saveButton = screen.queryByLabelText('Save to Memory');
        expect(saveButton).not.toBeNull();

        unmount();
      }),
      { numRuns: 10 },
    );
  });

  /**
   * Property 2 (Status Isolation): After calling save(session1), the status
   * for session2 should remain 'idle'. The post-fix hook returns a statusMap
   * keyed by sessionId.
   *
   * On UNFIXED code this FAILS because useMemorySave returns a single global
   * `status` field, not a per-session `statusMap`.
   *
   * **Validates: Requirements 2.2, 2.3**
   */
  it('should provide per-session status isolation via statusMap', async () => {
    await fc.assert(
      fc.asyncProperty(distinctSessionPairArb, async ([session1, session2]) => {
        const { result } = renderHook(() => useMemorySave());

        // The post-fix interface should expose statusMap
        const hookReturn = result.current as Record<string, unknown>;

        // Assert that statusMap exists (it won't on unfixed code)
        expect(hookReturn).toHaveProperty('statusMap');
        expect(typeof hookReturn.statusMap).toBe('object');

        // Save for session1
        await act(async () => {
          await result.current.save(session1);
        });

        // After saving session1, session2's status should still be 'idle'
        const currentReturn = result.current as Record<string, unknown>;
        const statusMap = currentReturn.statusMap as Record<string, string>;
        expect(statusMap[session2] ?? 'idle').toBe('idle');
      }),
      { numRuns: 10 },
    );
  });
});
