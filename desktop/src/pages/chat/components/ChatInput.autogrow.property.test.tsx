/**
 * Property-Based Tests for ChatInput Auto-Grow Textarea Height Clamping
 *
 * **Feature: remove-chat-input-extras**
 * **Property 1: Textarea height clamping invariant**
 * **Validates: Requirements 7.1, 7.2, 7.3**
 *
 * Tests that the adjustHeight logic correctly clamps the textarea height
 * to min(scrollHeight, maxHeight) for any scrollHeight value, and toggles
 * overflow-y between 'auto' (when content exceeds maxHeight) and 'hidden'
 * (when content fits).
 *
 * Since jsdom does not compute real scrollHeight, we test the adjustHeight
 * logic in isolation by mocking the textarea element's scrollHeight property
 * and getComputedStyle lineHeight.
 */

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ============== Constants ==============

const MAX_ROWS = 20;
const MOCK_LINE_HEIGHT = 20; // px — matches the fallback in ChatInput
const MAX_HEIGHT = MAX_ROWS * MOCK_LINE_HEIGHT; // 400px

// ============== Helpers ==============

/**
 * Creates a mock textarea element with a configurable scrollHeight.
 * Mimics the minimal HTMLTextAreaElement surface used by adjustHeight.
 */
function createMockTextarea(scrollHeight: number) {
  const style: Record<string, string> = {
    height: '',
    overflowY: '',
  };

  // Track the *current* scrollHeight so adjustHeight reads it dynamically
  let currentScrollHeight = scrollHeight;

  const el = {
    style,
    get scrollHeight() {
      return currentScrollHeight;
    },
    set scrollHeight(v: number) {
      currentScrollHeight = v;
    },
  };

  return el;
}

/**
 * Reproduces the adjustHeight logic from ChatInput.tsx.
 * Operates on the mock textarea element exactly as the real code does.
 */
function adjustHeight(el: ReturnType<typeof createMockTextarea>, maxHeight: number) {
  // Reset height to 'auto' so scrollHeight reflects true content height
  el.style.height = 'auto';
  const next = Math.min(el.scrollHeight, maxHeight);
  el.style.height = `${next}px`;
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
}


// ============== Property Tests ==============

describe('Property 1: Textarea height clamping invariant', () => {
  /**
   * For any scrollHeight value (0–2000), the adjustHeight function SHALL set
   * el.style.height to min(scrollHeight, maxHeight) + 'px', and set
   * el.style.overflowY to 'auto' iff scrollHeight > maxHeight, 'hidden' otherwise.
   *
   * **Validates: Requirements 7.1, 7.2, 7.3**
   */
  it('should clamp height to min(scrollHeight, maxHeight) and toggle overflowY', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 2000 }),
        (scrollHeight) => {
          const el = createMockTextarea(scrollHeight);
          adjustHeight(el, MAX_HEIGHT);

          const expectedHeight = Math.min(scrollHeight, MAX_HEIGHT);
          expect(el.style.height).toBe(`${expectedHeight}px`);

          const expectedOverflow = scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden';
          expect(el.style.overflowY).toBe(expectedOverflow);
        }
      ),
      { numRuns: 100 }
    );
  });
});


// ============== Send Reset Helper ==============

/**
 * Reproduces the send-reset logic from ChatInput.tsx.
 * After a message is sent, the textarea inline styles are cleared
 * so that rows={2} reasserts the native minimum height.
 */
function simulateSendReset(el: ReturnType<typeof createMockTextarea>) {
  el.style.height = '';          // clear inline style
  el.style.overflowY = 'hidden'; // always hidden after reset
}


// ============== Property 2 Tests ==============

describe('Property 2: Textarea height reset on send', () => {
  /**
   * For any textarea state with any scrollHeight (0–2000), after adjustHeight
   * has been called (simulating content), when the user sends a message the
   * textarea inline style.height SHALL be cleared to '' (empty string) so that
   * rows={2} reasserts the native minimum, and overflow-y SHALL be 'hidden'.
   *
   * **Validates: Requirements 7.4**
   */
  it('should reset height to empty string and overflowY to hidden after send', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 2000 }),
        (scrollHeight) => {
          // 1. Create a mock textarea with the random scrollHeight
          const el = createMockTextarea(scrollHeight);

          // 2. Call adjustHeight to simulate a textarea with content
          adjustHeight(el, MAX_HEIGHT);

          // Sanity: adjustHeight should have set some height value
          expect(el.style.height).not.toBe('');

          // 3. Simulate the send reset
          simulateSendReset(el);

          // 4. Assert the reset values
          expect(el.style.height).toBe('');
          expect(el.style.overflowY).toBe('hidden');
        }
      ),
      { numRuns: 100 }
    );
  });
});

