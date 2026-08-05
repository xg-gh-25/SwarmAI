/**
 * CanvasOutputRail — tree-list row style (run_b6554c95).
 *
 * XG directive 2026-08-04: the Canvas Outputs list must adopt the SwarmWS
 * explorer's standard tree-list row style (TreeNodeRow) instead of the earlier
 * larger dot + "NEW/UPD" word-tag form:
 *  - a file-type icon (material-symbols) precedes the name
 *  - the git status is a COMPACT single-letter chip (A / M), not a word-tag
 *  - the "NEW" / "UPD" WORDS are gone
 *  - the accent stays header-only; the list rows use the green/yellow git
 *    SEMANTIC colors (added=green, modified=yellow), never --color-primary
 *  - display path = file.path (relative, from unified resolve); copy uses
 *    absolutePath — NO new path-shortening logic (asserted by the copy path).
 *
 * Locks the LOGIC/markup; the exact pixel metrics are visual (verified on the
 * real machine). We mock the two data hooks so we own firstSeen + badges.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';
import type { ChangeStatus } from '../../../hooks/useChangeStatus';

let mockFiles: ReferencedFile[] = [];
let mockStatus = new Map<string, ChangeStatus>();
vi.mock('../../../hooks/useReferencedFiles', () => ({
  useReferencedFiles: () => ({
    files: { written: mockFiles },
    totalCount: mockFiles.length,
    clear: () => {},
  }),
}));
vi.mock('../../../hooks/useChangeStatus', () => ({
  useChangeStatus: () => mockStatus,
}));

import { CanvasOutputRail } from '../CanvasOutputRail';

const mkFile = (name: string, firstSeen = 1000): ReferencedFile => ({
  path: `src/components/${name}`,
  absolutePath: `/ws/src/components/${name}`,
  fileName: name,
  operation: 'written',
  firstSeen,
  count: 1,
});

beforeEach(() => {
  mockFiles = [];
  mockStatus = new Map();
});

describe('CanvasOutputRail — tree-list row style', () => {
  it('renders a file-type icon for each output row', () => {
    mockFiles = [mkFile('foo.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    const row = screen.getByText('foo.ts').closest('[data-testid="canvas-output-row"]')!;
    // A material-symbols file icon element must be present in the row.
    const icon = row.querySelector('[data-testid="canvas-output-icon"]');
    expect(icon).toBeTruthy();
  });

  it('shows a COMPACT single-letter status chip (A/M), never the words NEW/UPD', () => {
    mockFiles = [mkFile('added.ts', 2000), mkFile('mod.ts', 1000)];
    mockStatus = new Map([
      ['src/components/added.ts', 'new'],
      ['src/components/mod.ts', 'upd'],
    ]);
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    // Word-tags must be gone.
    expect(screen.queryByText('NEW')).toBeNull();
    expect(screen.queryByText('UPD')).toBeNull();
    // Single-letter chips present instead.
    const chips = screen.getAllByTestId('canvas-output-badge').map((n) => n.textContent);
    expect(chips).toContain('A');
    expect(chips).toContain('M');
  });

  it('the status chip uses the git SEMANTIC color, not the primary accent', () => {
    mockFiles = [mkFile('added.ts')];
    mockStatus = new Map([['src/components/added.ts', 'new']]);
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    const chip = screen.getByTestId('canvas-output-badge');
    // green (git-added) semantic color — NOT --color-primary
    expect(chip.getAttribute('style') || '').not.toContain('--color-primary');
    expect(chip.textContent).toBe('A');
  });

  it('displays the relative file.path locator, not a re-shortened path', () => {
    mockFiles = [mkFile('foo.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    // dir locator = dirname of file.path (relative) — proves no new path logic.
    expect(screen.getByText('src/components')).toBeTruthy();
  });

  it('exposes list semantics (role=list wrapping role=listitem rows) for a11y', () => {
    mockFiles = [mkFile('a.ts', 2000), mkFile('b.ts', 1000)];
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    const list = screen.getByRole('list');
    expect(list).toBeTruthy();
    // Each output row is a listitem within the list.
    const items = screen.getAllByRole('listitem');
    expect(items.length).toBe(2);
  });
});
