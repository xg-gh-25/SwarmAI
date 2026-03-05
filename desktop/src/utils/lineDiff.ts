/**
 * Line-based diff computation using the Longest Common Subsequence (LCS) algorithm.
 *
 * This module provides a lightweight, dependency-free diff utility that compares
 * two text strings at line granularity. It is used by the FileEditorModal's
 * inline diff view to highlight added and removed lines.
 *
 * Key exports:
 * - `DiffLine`            — Interface describing a single diff output line
 * - `computeLineDiff()`   — Computes a line-based diff between two text strings
 *
 * The LCS approach was chosen over `diff-match-patch` to avoid a ~50KB dependency
 * while keeping the implementation simple (~40 lines for the core algorithm).
 * Performance is adequate for files up to ~10K lines.
 */

/** A single line in the diff output. */
export interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  /** 1-based line number in the original text (undefined for added lines). */
  oldLineNumber?: number;
  /** 1-based line number in the new text (undefined for removed lines). */
  newLineNumber?: number;
}

/**
 * Build the LCS length table for two arrays of strings.
 *
 * Returns a 2D array `dp` where `dp[i][j]` is the length of the LCS
 * of `oldLines[0..i-1]` and `newLines[0..j-1]`.
 */
function buildLCSTable(oldLines: string[], newLines: string[]): number[][] {
  const m = oldLines.length;
  const n = newLines.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array.from({ length: n + 1 }, () => 0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  return dp;
}

/**
 * Backtrack through the LCS table to extract the common subsequence indices.
 *
 * Returns an array of `[oldIndex, newIndex]` pairs (0-based) representing
 * lines that appear in both texts.
 */
function backtrackLCS(
  dp: number[][],
  oldLines: string[],
  newLines: string[],
): [number, number][] {
  const result: [number, number][] = [];
  let i = oldLines.length;
  let j = newLines.length;

  while (i > 0 && j > 0) {
    if (oldLines[i - 1] === newLines[j - 1]) {
      result.push([i - 1, j - 1]);
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }

  return result.reverse();
}

/**
 * Compute a line-based diff between two text strings.
 *
 * Splits both texts by newline, computes the LCS of the resulting line arrays,
 * then walks both arrays against the LCS to produce a sequence of `DiffLine`
 * entries:
 * - `'unchanged'` — line exists in both texts (has both line numbers)
 * - `'removed'`   — line only in the old text (has `oldLineNumber` only)
 * - `'added'`     — line only in the new text (has `newLineNumber` only)
 *
 * Line numbers are 1-based.
 */
/**
 * Simple sequential diff for large files (>5000 lines).
 * Compares lines one-by-one — O(n) memory, O(n) time.
 * Less accurate than LCS but avoids O(m×n) memory allocation.
 */
function sequentialDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const result: DiffLine[] = [];
  const maxLen = Math.max(oldLines.length, newLines.length);
  for (let i = 0; i < maxLen; i++) {
    const oldLine = i < oldLines.length ? oldLines[i] : undefined;
    const newLine = i < newLines.length ? newLines[i] : undefined;
    if (oldLine !== undefined && newLine !== undefined && oldLine === newLine) {
      result.push({ type: 'unchanged', content: oldLine, oldLineNumber: i + 1, newLineNumber: i + 1 });
    } else {
      if (oldLine !== undefined) {
        result.push({ type: 'removed', content: oldLine, oldLineNumber: i + 1 });
      }
      if (newLine !== undefined) {
        result.push({ type: 'added', content: newLine, newLineNumber: i + 1 });
      }
    }
  }
  return result;
}

export function computeLineDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');

  // For very large files, fall back to sequential comparison to avoid O(m×n) memory
  if (oldLines.length > 5000 || newLines.length > 5000) {
    return sequentialDiff(oldLines, newLines);
  }

  const dp = buildLCSTable(oldLines, newLines);
  const lcs = backtrackLCS(dp, oldLines, newLines);

  const result: DiffLine[] = [];
  let oldIdx = 0;
  let newIdx = 0;

  for (const [lcsOld, lcsNew] of lcs) {
    // Emit removed lines before this LCS match
    while (oldIdx < lcsOld) {
      result.push({
        type: 'removed',
        content: oldLines[oldIdx],
        oldLineNumber: oldIdx + 1,
      });
      oldIdx++;
    }

    // Emit added lines before this LCS match
    while (newIdx < lcsNew) {
      result.push({
        type: 'added',
        content: newLines[newIdx],
        newLineNumber: newIdx + 1,
      });
      newIdx++;
    }

    // Emit the unchanged LCS line
    result.push({
      type: 'unchanged',
      content: oldLines[lcsOld],
      oldLineNumber: oldIdx + 1,
      newLineNumber: newIdx + 1,
    });
    oldIdx++;
    newIdx++;
  }

  // Emit remaining removed lines after the last LCS match
  while (oldIdx < oldLines.length) {
    result.push({
      type: 'removed',
      content: oldLines[oldIdx],
      oldLineNumber: oldIdx + 1,
    });
    oldIdx++;
  }

  // Emit remaining added lines after the last LCS match
  while (newIdx < newLines.length) {
    result.push({
      type: 'added',
      content: newLines[newIdx],
      newLineNumber: newIdx + 1,
    });
    newIdx++;
  }

  return result;
}
