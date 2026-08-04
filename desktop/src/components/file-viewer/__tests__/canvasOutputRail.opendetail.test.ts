/**
 * Tests for outputRowOpenDetail — the pure helper that builds the swarm:open-file
 * event detail for a CanvasOutputRail row.
 *
 * PR-review surface (run_b8ea6d5c): a row opens on its DIFF (this file's changes) —
 * that IS the review experience the OUTPUTS list exists for. This SUPERSEDES the old
 * AC3 "outputs open on SOURCE, never auto-diff" decision (the user explicitly wants a
 * per-file changes/PR-review view). `baseRef` is RESERVED (threaded end-to-end) but
 * its ref-aware git wiring is a KNOWN ISSUE deferred this run — so the helper emits
 * `autoDiff:true` and omits baseRef (undefined) for now.
 */
import { describe, it, expect } from 'vitest';
import { outputRowOpenDetail } from '../CanvasOutputRail';

describe('outputRowOpenDetail (PR-review — opens on diff)', () => {
  it('opens a MODIFIED (upd) output on its DIFF (this file\'s changes)', () => {
    const d = outputRowOpenDetail('src/pages/index.tsx', 'upd');
    expect(d.path).toBe('src/pages/index.tsx');
    // The whole point of the PR-review surface: rows open on the diff view.
    expect(d.autoDiff).toBe(true);
  });

  it('opens a NEW output with autoDiff too (FileViewer soft-falls-back to source when no baseline)', () => {
    // autoDiff:true is safe even for a new file — FileViewer only opens the diff when
    // a committed baseline exists, else it shows source (no empty-diff panel).
    expect(outputRowOpenDetail('src/hooks/useLayout.ts', 'new').autoDiff).toBe(true);
  });

  it('opens an unbadged output on diff', () => {
    expect(outputRowOpenDetail('a/b.md', undefined).autoDiff).toBe(true);
  });

  it('omits baseRef when none is given (content/knowledge rows → HEAD baseline)', () => {
    // A row with no baseRef arg diffs against HEAD (correct for uncommitted immediate
    // rows). Only source-final rows pass a <sha>^ (see the carry test below).
    const d = outputRowOpenDetail('src/x.ts', 'upd');
    expect(d.baseRef).toBeUndefined();
  });

  it('uses absolutePath as the resolve anchor when present (source-final row fix)', () => {
    // A source-final row's display path is repo-relative (repo ≠ workspace) → the bare
    // path 404s at /workspace/file/resolve. The absolutePath resolves for both workspace
    // and source-repo files, so it is the anchor the click must send (run_b8ea6d5c MED).
    const d = outputRowOpenDetail('backend/core/foo.py', 'upd', '/repo/backend/core/foo.py');
    expect(d.path).toBe('/repo/backend/core/foo.py');
  });

  it('carries baseRef through (run_030dc98e — the this-run diff baseline)', () => {
    // A source-final row's <sha>^ must reach the open-file detail so FileViewer diffs
    // against the pre-run parent, not HEAD (which is empty for a committed file).
    const d = outputRowOpenDetail('backend/foo.py', 'upd', '/repo/backend/foo.py', 'abc1234^');
    expect(d.baseRef).toBe('abc1234^');
    // content/knowledge rows pass no baseRef → undefined → FileViewer defaults to HEAD.
    expect(outputRowOpenDetail('Knowledge/x.md', 'new').baseRef).toBeUndefined();
  });

  it('falls back to the display path when no absolutePath (content/knowledge rows)', () => {
    const d = outputRowOpenDetail('Knowledge/foo.md', 'new');
    expect(d.path).toBe('Knowledge/foo.md');
  });
});
