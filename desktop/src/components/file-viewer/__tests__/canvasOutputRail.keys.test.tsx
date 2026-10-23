/**
 * Canvas Artifacts list — the row's time label and ↑/↓ switching (Super Canvas
 * commit 2, docs/design/super-canvas.md).
 *
 * - clockOf: HH:MM for today, M/D for an earlier day, '' with no time.
 * - onOutputListKey (the rail's <ul> handler): on the focused list, ↓/↑ opens the next / previous openable
 *   row through the row's own click (the same OPEN_FILE_EVENT a mouse click sends);
 *   a deleted row is skipped; the ends do not wrap; other keys do nothing.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CanvasOutputRail, clockOf } from '../CanvasOutputRail';
import { OPEN_FILE_EVENT } from '../../common/MarkdownRenderer';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';

const mk = (name: string, firstSeen: number, extra: Partial<ReferencedFile> = {}): ReferencedFile => ({
  path: `src/${name}`, absolutePath: `/ws/src/${name}`, fileName: name,
  operation: 'written', firstSeen, count: 1, ...extra,
});

const opened: string[] = [];
const onOpen = (e: Event) => opened.push((e as CustomEvent).detail.path);
afterEach(() => { document.removeEventListener(OPEN_FILE_EVENT, onOpen); opened.length = 0; });

function List({ files, selected }: { files: ReferencedFile[]; selected?: string }) {
  return <CanvasOutputRail files={{ written: files }} selectedPath={selected} />;
}

describe('clockOf', () => {
  const now = new Date(2026, 9, 23, 13, 30);
  it('today → HH:MM', () => expect(clockOf(new Date(2026, 9, 23, 9, 5).getTime(), now)).toBe('09:05'));
  it('another day → M/D', () => expect(clockOf(new Date(2026, 9, 21, 9, 5).getTime(), now)).toBe('10/21'));
  it('no time → empty', () => { expect(clockOf(0, now)).toBe(''); expect(clockOf(undefined, now)).toBe(''); });
});

describe('Artifacts row', () => {
  it('shows its first-seen time', () => {
    const t = new Date(); t.setHours(10, 52, 0, 0);
    render(<CanvasOutputRail files={{ written: [mk('a.md', t.getTime())] }} />);
    expect(screen.getByTestId('canvas-output-time').textContent).toBe('10:52');
  });
});

describe('↑/↓ on the focused list', () => {
  // railOrder = newest first: c (3), b (2), a (1)
  const files = [mk('a.ts', 1000), mk('b.ts', 2000), mk('c.ts', 3000)];

  it('↓ opens the next row, ↑ the previous', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    const { rerender } = render(<List files={files} selected="src/c.ts" />);
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'ArrowDown' });
    expect(opened).toEqual(['/ws/src/b.ts']);
    rerender(<List files={files} selected="src/b.ts" />);
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'ArrowUp' });
    expect(opened).toEqual(['/ws/src/b.ts', '/ws/src/c.ts']);
  });

  it('does not wrap at the ends, and other keys do nothing', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    render(<List files={files} selected="src/a.ts" />);
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'ArrowDown' });
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'Enter' });
    expect(opened).toEqual([]);
  });

  it('skips a deleted row', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    const withGone = [mk('a.ts', 1000), mk('b.ts', 2000, { deleted: true }), mk('c.ts', 3000)];
    render(<List files={withGone} selected="src/c.ts" />);
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'ArrowDown' });
    expect(opened).toEqual(['/ws/src/a.ts']);
  });

  it('with nothing selected, ↓ opens the first row', () => {
    document.addEventListener(OPEN_FILE_EVENT, onOpen);
    render(<List files={files} />);
    fireEvent.keyDown(screen.getByTestId('canvas-output-rail'), { key: 'ArrowDown' });
    expect(opened).toEqual(['/ws/src/c.ts']);
  });
});
