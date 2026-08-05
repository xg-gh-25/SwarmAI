/**
 * CanvasOutputRail — browsing-row + empty-state guard (run_5b330415).
 *
 * Canvas is used two ways: (1) the agent WRITES a file this session → it lands in
 * the `written` outputs list; (2) the user clicks a file link in chat to BROWSE a
 * file that was NOT written this session. Before this change, case (2) left the
 * outputs list empty → the ~140px bee empty-state rendered ABOVE the file surface,
 * obscuring it.
 *
 * This locks the LOGIC:
 *  - A `selectedPath` NOT in the written set is injected as a single "Browsing" row.
 *  - The bee empty-state renders ONLY when there are no written outputs AND no
 *    browsing file (a genuinely empty Canvas).
 *  - The browsing row does NOT inflate onCounts (counts stay written-only).
 *  - Dedup: a selectedPath that MATCHES a written row (by display path OR resolved
 *    absolutePath) is NOT re-injected as a browsing row (no duplicate).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';

let mockFiles: ReferencedFile[] = [];
vi.mock('../../../hooks/useReferencedFiles', () => ({
  useReferencedFiles: () => ({
    files: { written: mockFiles },
    totalCount: mockFiles.length,
    clear: () => {},
  }),
}));
vi.mock('../../../hooks/useChangeStatus', () => ({
  useChangeStatus: () => new Map(),
}));

import { CanvasOutputRail } from '../CanvasOutputRail';

const mkFile = (name: string, firstSeen = 1000): ReferencedFile => ({
  path: `src/${name}`,
  absolutePath: `/ws/src/${name}`,
  fileName: name,
  operation: 'written',
  firstSeen,
  count: 1,
});

beforeEach(() => { mockFiles = []; });

describe('CanvasOutputRail — browsing row + empty guard', () => {
  it('shows the bee empty-state when NO outputs AND no selectedPath', () => {
    mockFiles = [];
    render(<CanvasOutputRail files={{ written: mockFiles }} />);
    expect(screen.getByTestId('canvas-output-rail-empty')).toBeTruthy();
  });

  it('does NOT show the bee empty-state when a file is being browsed (selectedPath set, no outputs)', () => {
    mockFiles = [];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    expect(screen.queryByTestId('canvas-output-rail-empty')).toBeNull();
    // The browsed file appears as a row.
    expect(screen.getByText('deck.html')).toBeTruthy();
    expect(screen.getByTestId('canvas-browsing-row')).toBeTruthy();
  });

  it('always labels the Browsing row (even with NO written outputs — a lone row needs context)', () => {
    mockFiles = [];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    expect(screen.getByText('Browsing')).toBeTruthy();
    expect(screen.getByTestId('canvas-browsing-row')).toBeTruthy();
  });

  it('injects the browsed file as a Browsing row when it is NOT a written output', () => {
    mockFiles = [mkFile('written.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    expect(screen.getByText('written.ts')).toBeTruthy();     // written row
    expect(screen.getByText('deck.html')).toBeTruthy();       // browsing row
    expect(screen.getByTestId('canvas-browsing-row')).toBeTruthy();
  });

  it('does NOT duplicate: a selectedPath matching a written row (by display path) is not re-injected', () => {
    mockFiles = [mkFile('written.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="src/written.ts" />);
    expect(screen.queryByTestId('canvas-browsing-row')).toBeNull();
    // only one row for written.ts
    expect(screen.getAllByText('written.ts')).toHaveLength(1);
  });

  it('does NOT duplicate: a selectedPath matching a written row by resolved absolutePath is not re-injected', () => {
    mockFiles = [mkFile('written.ts')]; // absolutePath = /ws/src/written.ts
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/ws/src/written.ts" />);
    expect(screen.queryByTestId('canvas-browsing-row')).toBeNull();
    expect(screen.getAllByText('written.ts')).toHaveLength(1);
  });

  it('does NOT inflate onCounts with the browsing row (counts stay written-only)', () => {
    mockFiles = [mkFile('written.ts')];
    const onCounts = vi.fn();
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" onCounts={onCounts} />);
    // last call reflects written outputs only (total=1), browsing excluded
    const last = onCounts.mock.calls.at(-1)?.[0];
    expect(last).toEqual({ total: 1, neu: 0, upd: 0 });
  });

  // ── region A is accent-FREE: ONLY the header carries the primary accent ──
  // (run_02c5e32a). Selection/browse use neutral greys (--color-hover / --color-
  // border), never --color-primary — a colored fill on a list row read as
  // "region A is also accented", which the header-only rule forbids.
  it('selected output row carries NO primary accent, and its fill differs from hover', () => {
    mockFiles = [mkFile('written.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="src/written.ts" />);
    const row = screen.getByTestId('canvas-output-row');
    expect(row.className).not.toContain('color-primary');
    // Selected fill = --color-border (a STRONGER neutral than the --color-hover the
    // hover state uses) so a selected row is distinguishable from a merely-hovered
    // one (Gate-2 F2: same var for both collided the two states).
    expect(row.className).toContain('bg-[var(--color-border)]');
    expect(row.className).not.toContain('bg-[var(--color-hover)]');
    // left-bar is neutral (text-muted), never primary
    expect(row.innerHTML).not.toContain('bg-[var(--color-primary)]');
  });

  it('browsing row carries NO primary accent (neutral fill + neutral bar)', () => {
    mockFiles = [];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    const row = screen.getByTestId('canvas-browsing-row');
    expect(row.className).not.toContain('color-primary');
    expect(row.className).toContain('bg-[var(--color-border)]');
    // the left-bar (::before span) is neutral, not primary
    expect(row.innerHTML).not.toContain('bg-[var(--color-primary)]');
  });
});
