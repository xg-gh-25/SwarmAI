/**
 * Streaming-jank fix — streaming text renders as lightweight plaintext.
 *
 * Root cause (run_00e0e872): a text block streamed through MarkdownRenderer
 * re-parses the FULL markdown (4 remark/rehype plugins + KaTeX + highlight.js)
 * on every token, because block.text grows per token via MessageStore.updateLast.
 * That is O(n²) over the stream and causes perceptible jank on long replies.
 *
 * Fix: while isStreaming=true, render the text block as plaintext
 * (whitespace-pre-wrap, no markdown parse). When isStreaming=false (final /
 * historical messages), render via MarkdownRenderer — the UNCHANGED production
 * path every historical message already uses.
 *
 * Behavioral discriminator (no mocks): markdown like "# Heading" parses to an
 * <h1> element when rendered as markdown, but appears as the literal text
 * "# Heading" (no <h1>) when rendered as plaintext. We assert:
 *   - streaming  → NO <h1>, literal "#" text present (plaintext branch)
 *   - not stream → <h1> present (markdown branch, content identical)
 *
 * This is the leaf-component behavior test. The block ASSEMBLY guard lives in
 * AssistantMessageView.renderFidelity.test.tsx (unchanged).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
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

describe('ContentBlockRenderer — streaming text (jank fix run_00e0e872)', () => {
  const MD = '# Heading\n\nSome **bold** body text.';

  it('renders streaming text as PLAINTEXT — no markdown parse (no <h1>)', () => {
    const { container } = renderBlock(textBlock(MD), /* isStreaming */ true);
    // markdown would produce an <h1>; plaintext must not
    expect(container.querySelector('h1')).toBeNull();
    // the literal markdown source (including the '#') must be present verbatim
    expect(screen.getByText(/# Heading/)).toBeTruthy();
  });

  it('renders FINAL (non-streaming) text as MARKDOWN — <h1> present (unchanged path)', () => {
    const { container } = renderBlock(textBlock(MD), /* isStreaming */ false);
    const h1 = container.querySelector('h1');
    expect(h1).not.toBeNull();
    expect(h1?.textContent).toContain('Heading');
  });

  it('streaming→final transition preserves the SAME content (no loss at boundary)', () => {
    const block = textBlock(MD);
    const { container, rerender } = render(
      <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} isStreaming={true} />,
    );
    // during streaming, full text present as plaintext
    expect(container.textContent).toContain('Heading');
    expect(container.textContent).toContain('Some');
    expect(container.textContent).toContain('body text.');
    // flip to final — same content, now markdown-formatted
    rerender(
      <ContentBlockRenderer block={block} resultMap={emptyResultMap} allBlocks={[block]} isStreaming={false} />,
    );
    expect(container.querySelector('h1')).not.toBeNull();
    expect(container.textContent).toContain('Heading');
    expect(container.textContent).toContain('body text.');
  });

  it('handles empty / partial-markdown text safely while streaming', () => {
    // empty
    const { container: c1 } = renderBlock(textBlock(''), true);
    expect(c1).toBeTruthy(); // no throw
    // half-open markdown syntax (mid-stream token) must not crash and must show raw
    const { container: c2 } = renderBlock(textBlock('```py\ndef f('), true);
    expect(c2.textContent).toContain('def f(');
  });
});
