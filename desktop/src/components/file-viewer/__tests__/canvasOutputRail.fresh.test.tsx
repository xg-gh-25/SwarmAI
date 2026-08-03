/**
 * CanvasOutputRail land-pulse (v6 #4, run_09431085).
 *
 * A newly-ARRIVED output (firstSeen > the rail's mount time) gets ONE accent
 * land-pulse via the `.canvas-output-fresh` class. Outputs already present at
 * mount (session restore / tab switch) must NOT pulse (no pulse-storm on open).
 *
 * The animation itself is CSS (visual — verified on the real machine); this test
 * locks the LOGIC: which rows receive the fresh class.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { ReferencedFile } from '../../../hooks/useReferencedFiles';

// Control the two data hooks so we own firstSeen + badges.
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

const mkFile = (name: string, firstSeen: number): ReferencedFile => ({
  path: `src/${name}`,
  absolutePath: `/ws/src/${name}`,
  fileName: name,
  operation: 'written',
  firstSeen,
  count: 1,
});

beforeEach(() => { mockFiles = []; });

describe('CanvasOutputRail — land-pulse freshness', () => {
  it('does NOT pulse outputs that existed at mount (firstSeen in the past)', () => {
    const past = Date.now() - 10_000;
    mockFiles = [mkFile('old.ts', past)];
    render(<CanvasOutputRail sessionId="s1" />);
    const row = screen.getByText('old.ts').closest('[data-testid="canvas-output-row"]')!;
    expect(row.className).not.toContain('canvas-output-fresh');
  });

  it('pulses an output that arrived AFTER mount (firstSeen in the future)', () => {
    // A firstSeen after the rail mounts = it landed while the user was watching.
    const future = Date.now() + 60_000;
    mockFiles = [mkFile('brand-new.ts', future)];
    render(<CanvasOutputRail sessionId="s1" />);
    const row = screen.getByText('brand-new.ts').closest('[data-testid="canvas-output-row"]')!;
    expect(row.className).toContain('canvas-output-fresh');
  });
});
