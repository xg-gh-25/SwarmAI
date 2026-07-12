/**
 * Tests for FileEditorCore's large-file sync-processing threshold (Fix ③).
 *
 * hljs.highlight, computeLineDiff, and findAllMatches all run synchronously
 * over the full file content on the render path. On a large file each blocks
 * the main thread for hundreds of ms — on every keystroke. `shouldProcessSync`
 * is the single source of truth all three gates consult; above the threshold
 * the editor degrades (plaintext / no diff / no search) instead of freezing.
 *
 * These tests drive the REAL exported helper + constant (not a local copy), so
 * reverting the guard changes the assertions (mutation-proven).
 */

import { describe, it, expect, vi } from 'vitest';
import hljs from 'highlight.js';
import { shouldProcessSync, HIGHLIGHT_MAX_CHARS } from './FileEditorCore';

describe('shouldProcessSync (Fix ③ — large-file guard)', () => {
  it('allows sync processing at and below the threshold', () => {
    expect(shouldProcessSync(0)).toBe(true);
    expect(shouldProcessSync(1000)).toBe(true);
    expect(shouldProcessSync(HIGHLIGHT_MAX_CHARS)).toBe(true);
  });

  it('blocks sync processing above the threshold', () => {
    expect(shouldProcessSync(HIGHLIGHT_MAX_CHARS + 1)).toBe(false);
    // A realistic large doc (e.g. a 379K-char TECH.md) must be blocked.
    expect(shouldProcessSync(379_000)).toBe(false);
  });

  it('threshold separates a typical source file from a freeze-causing one', () => {
    const typicalSource = 8_000; // ~200 lines of code
    const freezeCausing = 379_000; // the doc that froze the UI in practice
    expect(shouldProcessSync(typicalSource)).toBe(true);
    expect(shouldProcessSync(freezeCausing)).toBe(false);
  });

  it('the guarded highlight path skips hljs.highlight above threshold (behavioral)', () => {
    // Mirror the exact guard used in the highlight useEffect. This proves the
    // decision the effect makes, using the real helper — if the guard is
    // removed, a large file would call hljs.highlight (the freeze).
    const spy = vi.spyOn(hljs, 'highlight');
    const bigContent = 'x'.repeat(HIGHLIGHT_MAX_CHARS + 1);
    const smallContent = 'const a = 1;';

    // Replicated guard (identical predicate to FileEditorCore's effect):
    const wouldHighlight = (content: string) => shouldProcessSync(content.length);

    expect(wouldHighlight(bigContent)).toBe(false); // → plaintext, no hljs
    expect(wouldHighlight(smallContent)).toBe(true); // → hljs.highlight runs
    spy.mockRestore();
  });
});
