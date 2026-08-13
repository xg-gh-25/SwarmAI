/**
 * P1 SSOT extraction (Canvas refactor, run_5d9178bf) — the two pure predicates
 * that de-duplicate the 4 inline kind filters (D2) and the 4 path-match sites
 * (D3, incl the BROKEN basename compare at useCanvasAutoSurface.ts:206).
 *
 * isRailKind(kind): does a review-verdict kind belong in the OUTPUTS rail?
 *   drop process + source (mid-run coding edit); keep content/knowledge/
 *   source-final/external-diff/external-nodiff; undefined (older backend) → keep.
 *
 * matchesPath(stored, event): does an incoming delete/change event refer to the
 *   same file as a stored row? exact display-path OR exact absolutePath OR an
 *   absolute event path ending with '/'+relative-stored-path. NEVER bare basename
 *   (the D3 bug — would false-match an unrelated same-named file across repos).
 */
import { describe, it, expect } from 'vitest';
import { isRailKind, matchesPath, samePath } from '../railSsot';

describe('isRailKind — SSOT for OUTPUTS rail membership', () => {
  it('keeps content and knowledge', () => {
    expect(isRailKind('content')).toBe(true);
    expect(isRailKind('knowledge')).toBe(true);
  });
  it('keeps source-final (pipeline-finish PR batch)', () => {
    expect(isRailKind('source-final')).toBe(true);
  });
  it('keeps the new external kinds', () => {
    expect(isRailKind('external-diff')).toBe(true);
    expect(isRailKind('external-nodiff')).toBe(true);
  });
  it('drops process (machine noise) and source (mid-run coding edit)', () => {
    expect(isRailKind('process')).toBe(false);
    expect(isRailKind('source')).toBe(false);
  });
  it('keeps undefined kind (older-backend migration, no regression)', () => {
    expect(isRailKind(undefined)).toBe(true);
  });
});

describe('matchesPath — SSOT anchored file-identity match (D3 basename bug fixed)', () => {
  const stored = { path: 'src/a.ts', absolutePath: '/ws/src/a.ts' };

  it('matches exact display path', () => {
    expect(matchesPath(stored, { path: 'src/a.ts' })).toBe(true);
  });
  it('matches exact absolutePath (event.path is the absolute form)', () => {
    expect(matchesPath(stored, { path: '/ws/src/a.ts' })).toBe(true);
  });
  it('matches when event.absolutePath equals stored.absolutePath', () => {
    expect(matchesPath(stored, { path: 'anything', absolutePath: '/ws/src/a.ts' })).toBe(true);
  });
  it('matches an absolute event path ending with /+relative stored path', () => {
    expect(matchesPath(stored, { path: '/some/other/root/src/a.ts' })).toBe(true);
  });
  it('does NOT match a same-basename file in a different repo (the D3 bug)', () => {
    // /other-repo/src/a.ts shares basename a.ts but is a DIFFERENT file — must NOT match.
    const s2 = { path: 'lib/a.ts', absolutePath: '/repo1/lib/a.ts' };
    expect(matchesPath(s2, { path: '/repo2/pkg/a.ts', absolutePath: '/repo2/pkg/a.ts' })).toBe(false);
  });
  it('does NOT match a partial suffix that is not path-segment-anchored', () => {
    // stored 'a.ts' must not match 'xa.ts' — the boundary is '/'+rel, not raw endsWith.
    const s3 = { path: 'a.ts', absolutePath: '/ws/a.ts' };
    expect(matchesPath(s3, { path: '/ws/xa.ts' })).toBe(false);
  });
});

describe('samePath — symmetric anchored path-string equality (D3 basename bug fixed)', () => {
  it('matches exact equality', () => {
    expect(samePath('/ws/src/a.ts', '/ws/src/a.ts')).toBe(true);
  });
  it('matches a resolved absolute vs its relative suffix (either order)', () => {
    expect(samePath('/ws/src/a.ts', 'src/a.ts')).toBe(true);
    expect(samePath('src/a.ts', '/ws/src/a.ts')).toBe(true);
  });
  it('does NOT match same-basename files in different repos (the D3 bug)', () => {
    expect(samePath('/repo1/src/a.ts', '/repo2/pkg/a.ts')).toBe(false);
  });
  it('does NOT match a non-segment-anchored suffix (a.ts vs xa.ts)', () => {
    expect(samePath('/ws/xa.ts', 'a.ts')).toBe(false);
  });
  it('returns false on null/undefined (no current or last-opened file)', () => {
    expect(samePath(null, 'a.ts')).toBe(false);
    expect(samePath('a.ts', undefined)).toBe(false);
    expect(samePath(null, null)).toBe(false);
  });
});
