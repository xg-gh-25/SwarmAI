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
import { render, screen, fireEvent } from '@testing-library/react';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';

let mockFiles: ReferencedFile[] = [];
vi.mock('../../../hooks/useReferencedFiles', () => ({
  useReferencedFiles: () => ({
    files: { written: mockFiles },
    totalCount: mockFiles.length,
    clear: () => {},
  }),
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

  // ── selection (Super Canvas, 2026-10-23) ──
  // A selected row (output or browsing) wears `.canvas-row-sel`: the content card's
  // surface + a 3px primary bar, so it reads as the file the card below shows.
  // (Supersedes run_02c5e32a's accent-free list.) Hover stays a different shade.
  it('selected output row wears the shared selection class and not the hover fill', () => {
    mockFiles = [mkFile('written.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="src/written.ts" />);
    const row = screen.getByTestId('canvas-output-row');
    expect(row.className).toContain('canvas-row-sel');
    expect(row.className).not.toContain('hover:bg-[var(--color-hover)]');
  });

  it('an unselected row is muted and hoverable, without the selection class', () => {
    mockFiles = [mkFile('a.ts'), mkFile('b.ts')];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="src/a.ts" />);
    const other = screen.getAllByTestId('canvas-output-row').find((r) => !r.hasAttribute('data-selected'))!;
    expect(other.className).not.toContain('canvas-row-sel');
    expect(other.className).toContain('text-[var(--color-text-muted)]');
    expect(other.className).toContain('hover:bg-[var(--color-hover)]');
  });

  it('browsing row wears the same selection class', () => {
    mockFiles = [];
    render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    expect(screen.getByTestId('canvas-browsing-row').className).toContain('canvas-row-sel');
  });

  it('the Browsing row has a × only when the panel wires stop-browsing, and it does not open the row', () => {
    mockFiles = [];
    const stop = vi.fn();
    const { rerender } = render(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" />);
    expect(screen.queryByTestId('canvas-browsing-close')).toBeNull();
    rerender(<CanvasOutputRail files={{ written: mockFiles }} selectedPath="/some/where/deck.html" onStopBrowsing={stop} />);
    const opened = vi.fn();
    document.addEventListener('swarm:open-file', opened);
    fireEvent.click(screen.getByTestId('canvas-browsing-close'));
    document.removeEventListener('swarm:open-file', opened);
    expect(stop).toHaveBeenCalledTimes(1);
    expect(opened).not.toHaveBeenCalled();
  });
});
