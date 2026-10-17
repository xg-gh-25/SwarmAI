/**
 * MarkdownRenderer — a file path in inline code carries its TYPE icon (v7.1).
 *
 * What is tested: an inline-code workspace path renders the same type icon the
 * Canvas OUTPUTS rail and the chat FileChip use (fileIcon: .md → description,
 * .pptx → slideshow, .html → html), desaturated so a type never reads as a
 * status, and still opens the file on click. Non-path code stays plain code.
 */
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import MarkdownRenderer, { OPEN_FILE_EVENT } from '../MarkdownRenderer';

describe('MarkdownRenderer — typed file references', () => {
  it.each([
    ['docs/plan.md', 'description'],
    ['out/deck.pptx', 'slideshow'],
    ['site/index.html', 'html'],
  ])('`%s` shows the %s icon and opens on click', (path, icon) => {
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    document.addEventListener(OPEN_FILE_EVENT, on);
    try {
      render(<MarkdownRenderer content={`see \`${path}\``} />);
      const ref = screen.getByRole('button', { name: new RegExp(path.replace('.', '\\.')) });
      const glyph = ref.querySelector('.material-symbols-outlined') as HTMLElement;
      expect(glyph.textContent).toBe(icon);
      expect(glyph.style.filter).toContain('saturate');
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
});
