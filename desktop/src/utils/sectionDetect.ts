/**
 * Section heading detection for markdown files.
 *
 * Shared by L2 (auto-diff summary) and L3 (review mode comments).
 * Walks backward from a given line to find the nearest `#`/`##`/`###` heading.
 *
 * Key exports:
 * - `findNearestHeading(lines, lineNumber)` — Returns heading text or null
 */

/**
 * Find the nearest markdown heading above `lineNumber` (1-based).
 * Returns the heading text (without `#` prefix) or null if none found.
 */
export function findNearestHeading(
  lines: string[],
  lineNumber: number,
): string | null {
  // lineNumber is 1-based; convert to 0-based and walk backward
  const startIdx = Math.min(lineNumber - 1, lines.length - 1);
  for (let i = startIdx; i >= 0; i--) {
    const trimmed = lines[i].trim();
    if (trimmed.startsWith('#')) {
      const heading = trimmed.replace(/^#+\s*/, '').trim();
      if (heading) return heading;
    }
  }
  return null;
}
