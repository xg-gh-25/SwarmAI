/**
 * mergeSessionArtifacts — the Artifacts rail = live rows + the session's persisted
 * artifacts (core/canvas_session), so a reloaded / restarted session still lists
 * what it produced.
 *
 * Invariants: a persisted file with no live row is added (kind, first time, turn
 * count, session id); a live row wins for the same file (by path or absolutePath);
 * a persisted file that no longer exists is marked deleted; a non-rail kind is not
 * added; nothing persisted returns the live object itself (no re-render churn).
 */
import { describe, it, expect } from 'vitest';
import { mergeSessionArtifacts, type GroupedReferencedFiles } from '../useReferencedFiles';

const live: GroupedReferencedFiles = {
  written: [{ path: 'Knowledge/a.md', absolutePath: '/ws/Knowledge/a.md', fileName: 'a.md',
    operation: 'written', firstSeen: Date.parse('2026-10-22T12:00:00'), count: 1, kind: 'knowledge' }],
};
const p = (path: string, extra: Record<string, unknown> = {}) => ({
  path, absolutePath: `/ws/${path}`, kind: 'content', firstAt: '2026-10-22T10:00:00', turns: 2, exists: true, ...extra,
});

describe('mergeSessionArtifacts', () => {
  it('adds persisted files, keeps live rows, marks missing ones deleted', () => {
    const out = mergeSessionArtifacts(live, [p('Knowledge/a.md', { kind: 'knowledge' }), p('Projects/P/deck.html'),
      p('Projects/P/gone.md', { exists: false, firstAt: '2026-10-22T09:00:00' })], 's1');
    expect(out.written.map((f) => f.path)).toEqual(['Knowledge/a.md', 'Projects/P/deck.html', 'Projects/P/gone.md']);
    expect(out.written[0]).toBe(live.written[0]);
    expect(out.written[1]).toMatchObject({ kind: 'content', count: 2, sessionId: 's1', deleted: false, fileName: 'deck.html' });
    expect(out.written[2].deleted).toBe(true);
  });

  it('skips non-rail kinds; nothing persisted returns the live object', () => {
    expect(mergeSessionArtifacts(live, [p('x.py', { kind: 'process' })]).written).toHaveLength(1);
    expect(mergeSessionArtifacts(live, undefined)).toBe(live);
    expect(mergeSessionArtifacts(live, [])).toBe(live);
  });
});
