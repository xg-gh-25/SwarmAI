/**
 * Unit tests for ``utils/sectionDetect.ts``.
 *
 * Tests the markdown heading detection utility shared by L2 (auto-diff
 * summary) and L3 (review mode comments).
 *
 * Key properties verified:
 * - Walks backward from given line to find nearest heading
 * - Returns null when no heading exists above
 * - Handles edge cases: line 1, empty lines, nested headings
 */

import { describe, it, expect } from 'vitest';
import { findNearestHeading } from '../utils/sectionDetect';

describe('findNearestHeading', () => {
  it('returns null for empty lines array', () => {
    expect(findNearestHeading([], 1)).toBeNull();
  });

  it('returns null when no heading exists above the line', () => {
    const lines = ['no heading here', 'just text', 'more text'];
    expect(findNearestHeading(lines, 3)).toBeNull();
  });

  it('finds heading on the same line', () => {
    const lines = ['# Title'];
    expect(findNearestHeading(lines, 1)).toBe('Title');
  });

  it('finds nearest heading above the target line', () => {
    const lines = [
      '# Top',
      '',
      '## Section A',
      'content under A',
      'more content',
    ];
    expect(findNearestHeading(lines, 5)).toBe('Section A');
  });

  it('returns the closest heading, not the first one', () => {
    const lines = [
      '# Title',
      '## Section 1',
      'text',
      '## Section 2',
      'text under section 2',
      'more text',
    ];
    expect(findNearestHeading(lines, 6)).toBe('Section 2');
  });

  it('handles h3 and deeper headings', () => {
    const lines = [
      '# Title',
      '## Section',
      '### Subsection',
      'content',
    ];
    expect(findNearestHeading(lines, 4)).toBe('Subsection');
  });

  it('skips empty heading lines (just # with no text)', () => {
    const lines = [
      '# Real Heading',
      '#',
      'content',
    ];
    // Line 3: walks back, finds '#' on line 2 but it has no text after stripping,
    // so continues to line 1 and finds 'Real Heading'
    expect(findNearestHeading(lines, 3)).toBe('Real Heading');
  });

  it('handles line number beyond array bounds gracefully', () => {
    const lines = ['# Title', 'text'];
    // lineNumber 100 should clamp to last line and walk back
    expect(findNearestHeading(lines, 100)).toBe('Title');
  });

  it('handles line number 1 with heading on line 1', () => {
    const lines = ['## First Heading', 'content'];
    expect(findNearestHeading(lines, 1)).toBe('First Heading');
  });

  it('handles line number 1 with no heading', () => {
    const lines = ['just text', '## Heading below'];
    expect(findNearestHeading(lines, 1)).toBeNull();
  });

  it('strips multiple # symbols correctly', () => {
    const lines = ['#### Deep Heading', 'content'];
    expect(findNearestHeading(lines, 2)).toBe('Deep Heading');
  });

  it('handles headings with extra whitespace', () => {
    const lines = ['  ##   Spaced Heading  ', 'content'];
    expect(findNearestHeading(lines, 2)).toBe('Spaced Heading');
  });
});
