import { describe, it, expect } from 'vitest';
import { truncateTitle } from './SessionTab';

/**
 * Unit tests for truncateTitle function
 * 
 * **Validates: Requirements 1.3**
 * - Each tab shows the session title, truncated to 25 characters with "..." if longer
 * 
 * Design doc constraint: Tab title truncation always produces valid output ≤28 chars (25 + "...")
 */
describe('truncateTitle', () => {
  describe('basic truncation behavior', () => {
    it('returns unchanged title when shorter than max length', () => {
      const title = 'Short title';
      const result = truncateTitle(title);
      
      expect(result).toBe('Short title');
      expect(result.length).toBeLessThanOrEqual(25);
    });

    it('returns unchanged title when exactly at max length', () => {
      const title = 'A'.repeat(25); // Exactly 25 characters
      const result = truncateTitle(title);
      
      expect(result).toBe(title);
      expect(result.length).toBe(25);
    });

    it('truncates and adds "..." when title exceeds max length', () => {
      const title = 'This is a very long title that exceeds the limit';
      const result = truncateTitle(title);
      
      expect(result).toBe('This is a very long title...');
      expect(result.length).toBe(28); // 25 + 3 for "..."
      expect(result.endsWith('...')).toBe(true);
    });

    it('truncates title that is exactly one character over max length', () => {
      const title = 'A'.repeat(26); // 26 characters
      const result = truncateTitle(title);
      
      expect(result).toBe('A'.repeat(25) + '...');
      expect(result.length).toBe(28);
    });
  });

  describe('custom max length parameter', () => {
    it('respects custom max length of 10', () => {
      const title = 'Hello World!';
      const result = truncateTitle(title, 10);
      
      expect(result).toBe('Hello Worl...');
      expect(result.length).toBe(13); // 10 + 3 for "..."
    });

    it('returns unchanged when title is under custom max length', () => {
      const title = 'Short';
      const result = truncateTitle(title, 10);
      
      expect(result).toBe('Short');
    });

    it('handles max length of 0', () => {
      const title = 'Any title';
      const result = truncateTitle(title, 0);
      
      expect(result).toBe('...');
      expect(result.length).toBe(3);
    });

    it('handles max length of 1', () => {
      const title = 'Hello';
      const result = truncateTitle(title, 1);
      
      expect(result).toBe('H...');
      expect(result.length).toBe(4);
    });
  });

  describe('empty string handling', () => {
    it('returns empty string when input is empty', () => {
      const result = truncateTitle('');
      
      expect(result).toBe('');
      expect(result.length).toBe(0);
    });

    it('returns empty string with custom max length', () => {
      const result = truncateTitle('', 10);
      
      expect(result).toBe('');
    });
  });

  describe('output length constraint', () => {
    it('output never exceeds maxLength + 3 (for "...")', () => {
      const testCases = [
        { title: 'Short', maxLength: 25 },
        { title: 'A'.repeat(100), maxLength: 25 },
        { title: 'Medium length title here', maxLength: 25 },
        { title: 'A'.repeat(50), maxLength: 10 },
        { title: 'Test', maxLength: 50 },
      ];

      for (const { title, maxLength } of testCases) {
        const result = truncateTitle(title, maxLength);
        const maxOutputLength = maxLength + 3;
        
        expect(result.length).toBeLessThanOrEqual(maxOutputLength);
      }
    });

    it('default max length produces output ≤28 chars (25 + "...")', () => {
      const longTitle = 'A'.repeat(1000);
      const result = truncateTitle(longTitle);
      
      expect(result.length).toBeLessThanOrEqual(28);
    });
  });

  describe('edge cases', () => {
    it('handles title with only spaces', () => {
      const title = '     ';
      const result = truncateTitle(title);
      
      expect(result).toBe('     ');
    });

    it('handles title with special characters', () => {
      const title = '🎉 Hello World! 🎉 This is a test';
      const result = truncateTitle(title);
      
      // Note: emoji may count as multiple characters depending on encoding
      expect(result.length).toBeLessThanOrEqual(28);
    });

    it('handles title with newlines', () => {
      const title = 'Line1\nLine2\nLine3 and more text here';
      const result = truncateTitle(title);
      
      expect(result.length).toBeLessThanOrEqual(28);
    });
  });
});
