/**
 * Tests for outputRowOpenDetail — the pure helper that builds the swarm:open-file
 * event detail for a CanvasOutputRail row.
 *
 * AC3 (single-gutter default): a modified ('upd') output must NOT auto-open on the
 * diff view (which renders TWO before|after line-number columns → reads as "double
 * line numbers"). It opens on SOURCE (single gutter); the user reaches the diff via
 * the editor's Show Changes toggle. This test is the RED→GREEN guard for that fix —
 * it fails against the pre-fix behavior (autoDiff: badge==='upd').
 */
import { describe, it, expect } from 'vitest';
import { outputRowOpenDetail } from '../CanvasOutputRail';

describe('outputRowOpenDetail (AC3 — single-gutter default)', () => {
  it('opens a MODIFIED (upd) output on SOURCE, not auto-diff', () => {
    const d = outputRowOpenDetail('src/pages/index.tsx', 'upd');
    expect(d.path).toBe('src/pages/index.tsx');
    // The whole point: no auto-diff → single gutter. Diff is reached via the toggle.
    expect(d.autoDiff).toBe(false);
  });

  it('opens a NEW output on source (no diff to show)', () => {
    expect(outputRowOpenDetail('src/hooks/useLayout.ts', 'new').autoDiff).toBe(false);
  });

  it('opens an unbadged output on source', () => {
    expect(outputRowOpenDetail('a/b.md', undefined).autoDiff).toBe(false);
  });
});
