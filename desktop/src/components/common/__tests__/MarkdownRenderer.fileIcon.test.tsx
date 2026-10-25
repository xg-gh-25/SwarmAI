/**
 * MarkdownRenderer — a file path in inline code carries its TYPE icon (v7.1).
 *
 * What is tested: an inline-code workspace path renders the same type icon the
 * Canvas OUTPUTS rail and the chat FileChip use (FileTypeIcon: .md → MD,
 * .pptx → PPT, .html → HTM), desaturated so a type never reads as a
 * status, and still opens the file on click. Non-path code stays plain code.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import MarkdownRenderer, { OPEN_FILE_EVENT } from '../MarkdownRenderer';

describe('MarkdownRenderer — typed file references', () => {
  it.each([
    ['docs/plan.md', 'MD'],
    ['out/deck.pptx', 'PPT'],
    ['site/index.html', 'HTM'],
  ])('`%s` shows the %s badge and opens on click', (path, icon) => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    try {
      render(<MarkdownRenderer content={`see \`${path}\``} />);
      const ref = screen.getByRole('button', { name: new RegExp(path.replace('.', '\\.')) });
      const glyph = ref.querySelector('.file-badge') as HTMLElement;
      expect(glyph.textContent).toBe(icon);
      expect(glyph.className).toContain('file-badge-muted');
      expect(ref.textContent).not.toContain('open_in_new');
      fireEvent.click(ref);
      expect(seen).toEqual([{ path }]);
    } finally {
      document.removeEventListener(OPEN_FILE_EVENT, on);
    }
  });

  it('plain inline code is not a file reference', () => {
    render(<MarkdownRenderer content="call `foo()` now" />);
    expect(screen.queryByRole('button')).toBeNull();
  });

  // design 2026-10-26-session-products.md §3.4: a reference reads as a link.
  it('a file reference looks like a link and says it opens in the Canvas', () => {
    render(<MarkdownRenderer content={'see `docs/plan.md` and `foo()`'} />);
    const ref = screen.getByRole('button', { name: /docs\/plan\.md/ });
    expect(ref.getAttribute('title')).toBe('Open in Canvas');
    expect(ref.className).toContain('text-primary');
    expect(ref.className).toContain('underline');
    const plain = screen.getByText('foo()');
    expect(plain.className).not.toContain('text-primary');
    expect(plain.className).not.toContain('underline');
  });
});
