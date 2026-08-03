/**
 * Streaming-render policy — markdown DURING streaming, throttled to bound reparse cost.
 *
 * History: run_00e0e872 (e96b45a9) rendered streaming text as PLAINTEXT to kill
 * O(n²) reparse jank — MarkdownRenderer re-parses the FULL string via its
 * remark/rehype plugin chain (remarkGfm + remarkBreaks + remarkMath + rehypeKatex;
 * highlight.js runs post-render per CodeBlock, not in this parse) on every token,
 * and block.text grows per token via MessageStore.updateLast → O(n²). That fixed
 * the jank but lost live markdown (headings/lists/code only appeared at stream end,
 * with a visible reflow jump).
 *
 * Current policy (run_087e097e, window widened to 200ms in run_954d7c48): render
 * MARKDOWN during streaming too, but THROTTLE the re-render to ~200ms so the
 * expensive parse runs ~5×/sec instead of once per token. This keeps O(n²) away
 * (parse count is bounded by time, not token count) while showing formatted
 * markdown live. Tests settle the throttle with advanceTimersByTime(200).
 *
 * Behavioral discriminator (no mocks): "# Heading" parses to an <h1> when rendered as
 * markdown, but is literal "# Heading" text as plaintext. We assert:
 *   - streaming  → <h1> present (markdown branch) once the throttle has settled
 *   - not stream → <h1> present (markdown branch, identical resting state)
 *   - throttle   → rapid token updates do NOT each trigger an immediate reparse;
 *                  the rendered content lags until the throttle window elapses,
 *                  then catches up to the latest text (trailing edge).
 *
 * This is the leaf-component behavior test. The block ASSEMBLY guard lives in
 * AssistantMessageView.renderFidelity.test.tsx (unchanged).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';
import { ContentBlockRenderer } from '../ContentBlockRenderer';
import type { ContentBlock, ToolResultContent } from '../../../../types';

const emptyResultMap = new Map<string, ToolResultContent>();

function textBlock(text: string): ContentBlock {
  return { type: 'text', text } as ContentBlock;
}

function renderBlock(block: ContentBlock, isStreaming: boolean) {
  return render(
    <ContentBlockRenderer
      block={block}
      resultMap={emptyResultMap}
      allBlocks={[block]}
      isStreaming={isStreaming}
    />,
  );
}

describe('ContentBlockRenderer — streaming text (throttled markdown, run_087e097e)', () => {
  const MD = '# Heading\n\nSome **bold** body text.';

  describe('without fake timers (resting-state behavior)', () => {
    it('renders FINAL (non-streaming) text as MARKDOWN — <h1> present (unchanged path)', () => {
      const { container } = renderBlock(textBlock(MD), /* isStreaming */ false);
      const h1 = container.querySelector('h1');
      expect(h1).not.toBeNull();
      expect(h1?.textContent).toContain('Heading');
    });

    it('handles empty / partial-markdown text safely while streaming (no crash)', () => {
      const { container: c1 } = renderBlock(textBlock(''), true);
      expect(c1).toBeTruthy(); // no throw on empty
      // half-open markdown syntax (mid-stream token) must not crash and must show the text
      const { container: c2 } = renderBlock(textBlock('```py\ndef f('), true);
      expect(c2.textContent).toContain('def f(');
    });
  });

  describe('with fake timers (throttle behavior)', () => {
    beforeEach(() => {
      // Relies on vitest's default fakeTimers.toFake INCLUDING 'Date' — the throttle
      // compares Date.now() against a ref, so Date must advance with the fake clock.
      // If a future vite.config sets an explicit toFake list that omits 'Date', this
      // throttle assertion would silently break — keep 'Date' faked.
      vi.useFakeTimers();
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it('renders streaming text as MARKDOWN — <h1> present once throttle settles', () => {
      const { container } = renderBlock(textBlock(MD), /* isStreaming */ true);
      // advance past the throttle window so the trailing-edge render fires
      act(() => {
        vi.advanceTimersByTime(200);
      });
      expect(container.querySelector('h1')).not.toBeNull();
      expect(screen.getByText('Heading')).toBeTruthy();
    });

    it('throttles reparse — rapid token growth does NOT render every intermediate state immediately', () => {
      const block = textBlock('# H');
      const { container, rerender } = render(
        <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} isStreaming={true} />,
      );
      // leading edge: first render shows initial content as markdown
      act(() => { vi.advanceTimersByTime(200); });
      expect(container.querySelector('h1')?.textContent).toContain('H');

      // Now push 5 rapid token updates inside ONE throttle window (50ms total, well
      // within the 200ms window). The throttle must NOT immediately reflect each value.
      for (let i = 0; i < 5; i++) {
        rerender(
          <ContentBlockRenderer
            block={textBlock(`# H${'i'.repeat(i + 1)}`)}
            resultMap={emptyResultMap}
            allBlocks={[textBlock(`# H${'i'.repeat(i + 1)}`)]}
            isStreaming={true}
          />,
        );
        act(() => { vi.advanceTimersByTime(10); }); // 10ms between tokens, all within one 200ms window
      }
      // Within the window the rendered heading should still lag behind the latest "# Hiiiii".
      const midText = container.querySelector('h1')?.textContent ?? '';
      expect(midText.length).toBeLessThan('Hiiiii'.length);

      // After the throttle window elapses, trailing edge catches up to the LATEST text.
      act(() => { vi.advanceTimersByTime(200); });
      expect(container.querySelector('h1')?.textContent).toContain('Hiiiii');
    });

    it('streaming→final transition preserves the latest content (no loss at boundary)', () => {
      const block = textBlock(MD);
      const { container, rerender } = render(
        <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} isStreaming={true} />,
      );
      act(() => { vi.advanceTimersByTime(200); });
      expect(container.textContent).toContain('Heading');

      // flip to final — same content, markdown-formatted, nothing dropped
      rerender(
        <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} isStreaming={false} />,
      );
      expect(container.querySelector('h1')).not.toBeNull();
      expect(container.textContent).toContain('Heading');
      expect(container.textContent).toContain('body text.');
    });
  });
});
