/**
 * Tests for the Signals nav click resolution (bugfix run_a73566c4, 2026-07-12).
 *
 * BUG: handleSignalsClick hardcoded `Knowledge/Signals/<today>-digest.md`. On any
 * day before the digest scheduled job runs, that file doesn't exist → FileEditor
 * shows "file not found". FIX: resolve the LATEST existing *-digest.md from the
 * Signals directory listing instead of assuming today.
 *
 * `pickLatestDigest` is the pure resolution core (exported for test). It takes the
 * /workspace/tree/expand children and returns the newest digest path, or null.
 * Names are YYYY-MM-DD-digest.md → lexical max == chronological latest. *-weekly.md
 * (same directory) must be excluded.
 */
import { describe, it, expect } from 'vitest';
import { pickLatestDigest } from './ThreeColumnLayout';

type Child = { name: string; path: string };

const mk = (names: string[]): Child[] =>
  names.map((n) => ({ name: n, path: `Knowledge/Signals/${n}` }));

describe('pickLatestDigest', () => {
  it('returns the lexically-latest -digest.md path', () => {
    const children = mk([
      '2026-07-09-digest.md',
      '2026-07-11-digest.md',
      '2026-07-10-digest.md',
    ]);
    expect(pickLatestDigest(children)).toBe('Knowledge/Signals/2026-07-11-digest.md');
  });

  it('excludes -weekly.md files (same directory)', () => {
    const children = mk([
      '2026-07-11-digest.md',
      '2026-07-11-weekly.md', // lexically > digest but must be ignored
    ]);
    expect(pickLatestDigest(children)).toBe('Knowledge/Signals/2026-07-11-digest.md');
  });

  it('returns null when no digest files exist', () => {
    expect(pickLatestDigest(mk(['2026-07-11-weekly.md', 'README.md']))).toBeNull();
    expect(pickLatestDigest([])).toBeNull();
  });

  it('opens today when today digest is present (latest == today)', () => {
    const children = mk([
      '2026-07-11-digest.md',
      '2026-07-12-digest.md', // today
    ]);
    expect(pickLatestDigest(children)).toBe('Knowledge/Signals/2026-07-12-digest.md');
  });

  it('ignores entries without name/path safely', () => {
    const dirty = [
      { name: '2026-07-11-digest.md', path: 'Knowledge/Signals/2026-07-11-digest.md' },
      { name: '', path: '' } as Child,
    ];
    expect(pickLatestDigest(dirty)).toBe('Knowledge/Signals/2026-07-11-digest.md');
  });
});
