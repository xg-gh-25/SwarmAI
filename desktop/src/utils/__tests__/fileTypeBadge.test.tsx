/**
 * fileTypeBadge + FileTypeIcon — the one file-type mark (XG 2026-10-23, option A).
 *
 * - Known extensions get their own label (MD, TS, JS, PY, {}, YML, PDF …); variants
 *   of one type share it (tsx → TS, yml → YML).
 * - An unknown extension shows its first 3 letters; an extensionless name its
 *   first 3 letters, except the known Dockerfile (DK) / Makefile (MK).
 * - Only the last path segment counts (a dotted folder does not leak in); a
 *   leading-dot file uses what follows the dot (.env → ENV).
 * - Every colour is a theme token (no literal hex).
 * - FileTypeIcon draws the label, carries the colour in --fb, honours size /
 *   muted / colour override.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { fileTypeBadge } from '../fileUtils';
import { FileTypeIcon } from '../../components/common/FileTypeIcon';

describe('fileTypeBadge', () => {
  it.each([
    ['README.md', 'MD'], ['App.tsx', 'TS'], ['x.ts', 'TS'], ['index.js', 'JS'], ['engine.py', 'PY'],
    ['config.json', '{}'], ['a.yml', 'YML'], ['a.yaml', 'YML'], ['report.pdf', 'PDF'],
    ['deck.pptx', 'PPT'], ['site.html', 'HTM'], ['logo.svg', 'SVG'], ['p.png', 'IMG'],
    ['run.sh', 'SH'], ['Dockerfile', 'DK'], ['Makefile', 'MK'], ['notes.weird', 'WEI'],
    ['LICENSE', 'LIC'], ['.env', 'ENV'], ['a/b.c/file', 'FIL'], ['/abs/path/to/x.ts', 'TS'],
  ])('%s → %s', (name, label) => {
    expect(fileTypeBadge(name).label).toBe(label);
  });

  it('colours are theme tokens', () => {
    for (const n of ['a.md', 'a.ts', 'a.py', 'a.pdf', 'a.zzz', 'Dockerfile']) {
      expect(fileTypeBadge(n).color).toMatch(/^var\(--color-[a-z-]+\)$/);
    }
  });
});

describe('FileTypeIcon', () => {
  it('draws the label with its colour, size and mute', () => {
    const { container } = render(<FileTypeIcon name="a.ts" size="md" muted />);
    const el = container.querySelector('.file-badge') as HTMLElement;
    expect(el.textContent).toBe('TS');
    expect(el.className).toContain('file-badge-md');
    expect(el.className).toContain('file-badge-muted');
    expect(el.style.getPropertyValue('--fb')).toBe('var(--color-icon-typescript)');
    expect(el.getAttribute('aria-hidden')).toBe('true');
  });

  it('a colour override wins (the file tree tints by git status)', () => {
    const { container } = render(<FileTypeIcon name="a.ts" color="var(--color-git-modified)" />);
    expect((container.querySelector('.file-badge') as HTMLElement).style.getPropertyValue('--fb')).toBe('var(--color-git-modified)');
  });
});
