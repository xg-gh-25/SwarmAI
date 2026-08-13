/**
 * Tests for outputRowOpenDetail — the pure helper that builds the swarm:open-file
 * event detail for a CanvasOutputRail row.
 *
 * CONTENT-DEFAULT (run_d3cc1f2c, XG directive reversing run_b8ea6d5c's PR-review
 * default): a row (and pipeline-finish auto-open) renders the file's CONTENT, NOT a
 * diff. Rationale (XG): "Canvas 看的是 changes list; diff 是文件上的一个操作,不是
 * canvas 要不要渲染的判断" — the diff is a per-file Show-Changes TOGGLE inside
 * FileEditorCore, never the open-default. This also structurally eliminates the
 * empty-diff BLANK render (a committed file whose diff is empty no longer opens into
 * an empty DiffView) WITHOUT adding a DiffView empty-state layer — the wrong-layer
 * patch XG explicitly rejected. `baseRef` is STILL threaded end-to-end so the Show
 * Changes toggle diffs against the correct pre-run baseline; only the OPEN default
 * changed (autoDiff:false).
 */
import { describe, it, expect } from 'vitest';
import { outputRowOpenDetail } from '../CanvasOutputRail';

describe('outputRowOpenDetail (content-default — diff is a toggle, not the open default)', () => {
  it('opens a MODIFIED (upd) output on its CONTENT, not a diff', () => {
    const d = outputRowOpenDetail('src/pages/index.tsx', 'upd');
    expect(d.path).toBe('src/pages/index.tsx');
    // XG directive: rows render content; the diff is the Show Changes toggle.
    expect(d.autoDiff).toBe(false);
  });

  it('opens a NEW output on content (never a blank/empty diff)', () => {
    expect(outputRowOpenDetail('src/hooks/useLayout.ts', 'new').autoDiff).toBe(false);
  });

  it('opens an unbadged output on content', () => {
    expect(outputRowOpenDetail('a/b.md', undefined).autoDiff).toBe(false);
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

  it('carries sessionId through for an external row (run_c014a4f3 render-gate)', () => {
    // An external (outside-SwarmWS) surfaced row carries its owning session id so the
    // render fetch can pass session_id → GET /workspace/file allows the outside-$HOME
    // path read-only. Internal rows pass no sessionId → undefined → home-only guard.
    const d = outputRowOpenDetail('extrepo/hello.py', 'new', '/private/tmp/extrepo/hello.py', undefined, 'sess-xyz');
    expect(d.sessionId).toBe('sess-xyz');
    expect(outputRowOpenDetail('Knowledge/x.md', 'new').sessionId).toBeUndefined();
  });
});
