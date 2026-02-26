/**
 * Property-Based Tests for Chat Utilities
 *
 * **Feature: chat-utilities**
 * **Property 1: Session Grouping by Time**
 * **Property 2: Timestamp Formatting**
 * **Validates: Chat session organization and time display**
 *
 * These tests validate the core utility functions for chat session management.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as fc from 'fast-check';
import type { ChatSession } from '../../types';
import { groupSessionsByTime, formatTimestamp } from './utils';
import { MS_PER_DAY } from './constants';

// ============== Arbitraries ==============

/**
 * Arbitrary for generating valid chat sessions
 */
const chatSessionArb = (dateArb: fc.Arbitrary<Date>): fc.Arbitrary<ChatSession> =>
  fc.record({
    id: fc.uuid(),
    agentId: fc.uuid(),
    title: fc.string({ minLength: 1, maxLength: 100 }),
    lastAccessedAt: dateArb.map((d) => d.toISOString()),
    createdAt: dateArb.map((d) => d.toISOString()),
    messageCount: fc.integer({ min: 0, max: 1000 }),
  });

/**
 * Arbitrary for generating dates within a specific range
 */
const dateInRangeArb = (startMs: number, endMs: number): fc.Arbitrary<Date> =>
  fc.integer({ min: startMs, max: endMs }).map((ms) => new Date(ms));

// ============== Property-Based Tests ==============

describe('Chat Utilities - Property-Based Tests', () => {
  /**
   * Property 1: Session Grouping by Time
   * **Feature: chat-utilities, Property 1: Session Grouping by Time**
   *
   * For any list of chat sessions, groupSessionsByTime SHALL correctly
   * categorize sessions into today, yesterday, thisWeek, thisMonth, and older.
   */
  describe('Feature: chat-utilities, Property 1: Session Grouping by Time', () => {
    // Use a fixed "now" for deterministic testing
    const fixedNow = new Date('2025-02-19T12:00:00.000Z');
    const fixedNowMs = fixedNow.getTime();

    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(fixedNow);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should return empty array for empty sessions list', () => {
      fc.assert(
        fc.property(fc.constant([] as ChatSession[]), (sessions: ChatSession[]) => {
          const result = groupSessionsByTime(sessions);
          expect(result).toEqual([]);
        }),
        { numRuns: 10 }
      );
    });

    it('should group today sessions correctly', () => {
      // Today: same calendar day as fixedNow
      const todayStart = new Date(fixedNow.getFullYear(), fixedNow.getMonth(), fixedNow.getDate());
      const todayEnd = new Date(todayStart.getTime() + MS_PER_DAY - 1);

      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(todayStart.getTime(), todayEnd.getTime())), {
            minLength: 1,
            maxLength: 10,
          }),
          (sessions) => {
            const result = groupSessionsByTime(sessions);

            // Property: All sessions from today SHALL be in 'today' group
            expect(result.length).toBe(1);
            expect(result[0].group).toBe('today');
            expect(result[0].sessions.length).toBe(sessions.length);
          }
        ),
        { numRuns: 50 }
      );
    });

    it('should group yesterday sessions correctly', () => {
      const todayStart = new Date(fixedNow.getFullYear(), fixedNow.getMonth(), fixedNow.getDate());
      const yesterdayStart = new Date(todayStart.getTime() - MS_PER_DAY);
      const yesterdayEnd = new Date(todayStart.getTime() - 1);

      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(yesterdayStart.getTime(), yesterdayEnd.getTime())), {
            minLength: 1,
            maxLength: 10,
          }),
          (sessions) => {
            const result = groupSessionsByTime(sessions);

            // Property: All sessions from yesterday SHALL be in 'yesterday' group
            expect(result.length).toBe(1);
            expect(result[0].group).toBe('yesterday');
            expect(result[0].sessions.length).toBe(sessions.length);
          }
        ),
        { numRuns: 50 }
      );
    });

    it('should preserve all sessions across groups', () => {
      // Generate sessions across different time periods
      const oneMonthAgo = fixedNowMs - 30 * MS_PER_DAY;

      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(oneMonthAgo, fixedNowMs)), {
            minLength: 1,
            maxLength: 20,
          }),
          (sessions) => {
            const result = groupSessionsByTime(sessions);

            // Property: Total sessions across all groups SHALL equal input sessions
            const totalGroupedSessions = result.reduce((sum, g) => sum + g.sessions.length, 0);
            expect(totalGroupedSessions).toBe(sessions.length);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should return groups in correct order', () => {
      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(fixedNowMs - 60 * MS_PER_DAY, fixedNowMs)), {
            minLength: 5,
            maxLength: 20,
          }),
          (sessions) => {
            const result = groupSessionsByTime(sessions);
            const expectedOrder = ['today', 'yesterday', 'thisWeek', 'thisMonth', 'older'];

            // Property: Groups SHALL appear in chronological order
            let lastIndex = -1;
            for (const group of result) {
              const currentIndex = expectedOrder.indexOf(group.group);
              expect(currentIndex).toBeGreaterThan(lastIndex);
              lastIndex = currentIndex;
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should only return non-empty groups', () => {
      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(fixedNowMs - 60 * MS_PER_DAY, fixedNowMs)), {
            minLength: 0,
            maxLength: 20,
          }),
          (sessions) => {
            const result = groupSessionsByTime(sessions);

            // Property: All returned groups SHALL have at least one session
            for (const group of result) {
              expect(group.sessions.length).toBeGreaterThan(0);
            }
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should not mutate input sessions array', () => {
      fc.assert(
        fc.property(
          fc.array(chatSessionArb(dateInRangeArb(fixedNowMs - 30 * MS_PER_DAY, fixedNowMs)), {
            minLength: 1,
            maxLength: 10,
          }),
          (sessions) => {
            const originalLength = sessions.length;
            const originalIds = sessions.map((s) => s.id);

            groupSessionsByTime(sessions);

            // Property: Input array SHALL NOT be mutated
            expect(sessions.length).toBe(originalLength);
            expect(sessions.map((s) => s.id)).toEqual(originalIds);
          }
        ),
        { numRuns: 100 }
      );
    });
  });

  /**
   * Property 2: Timestamp Formatting
   * **Feature: chat-utilities, Property 2: Timestamp Formatting**
   *
   * For any valid timestamp, formatTimestamp SHALL return a human-readable
   * relative time string.
   */
  describe('Feature: chat-utilities, Property 2: Timestamp Formatting', () => {
    const fixedNow = new Date('2025-02-19T12:00:00.000Z');

    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(fixedNow);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('should return empty string for undefined timestamp', () => {
      fc.assert(
        fc.property(fc.constant(undefined), (timestamp) => {
          const result = formatTimestamp(timestamp);
          expect(result).toBe('');
        }),
        { numRuns: 10 }
      );
    });

    it('should return empty string for invalid timestamp', () => {
      fc.assert(
        fc.property(
          fc.oneof(fc.constant('invalid'), fc.constant('not-a-date'), fc.constant('')),
          (timestamp) => {
            const result = formatTimestamp(timestamp);
            expect(result).toBe('');
          }
        ),
        { numRuns: 30 }
      );
    });

    it('should return "Just now" for timestamps less than 1 minute ago', () => {
      fc.assert(
        fc.property(fc.integer({ min: 0, max: 59 }), (secondsAgo) => {
          const timestamp = new Date(fixedNow.getTime() - secondsAgo * 1000).toISOString();
          const result = formatTimestamp(timestamp);
          expect(result).toBe('Just now');
        }),
        { numRuns: 50 }
      );
    });

    it('should return minutes ago for timestamps 1-59 minutes ago', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 59 }), (minutesAgo) => {
          const timestamp = new Date(fixedNow.getTime() - minutesAgo * 60000).toISOString();
          const result = formatTimestamp(timestamp);
          expect(result).toBe(`${minutesAgo}m ago`);
        }),
        { numRuns: 50 }
      );
    });

    it('should return hours ago for timestamps 1-23 hours ago', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 23 }), (hoursAgo) => {
          const timestamp = new Date(fixedNow.getTime() - hoursAgo * 3600000).toISOString();
          const result = formatTimestamp(timestamp);
          expect(result).toBe(`${hoursAgo}h ago`);
        }),
        { numRuns: 50 }
      );
    });

    it('should return days ago for timestamps 1-6 days ago', () => {
      fc.assert(
        fc.property(fc.integer({ min: 1, max: 6 }), (daysAgo) => {
          const timestamp = new Date(fixedNow.getTime() - daysAgo * MS_PER_DAY).toISOString();
          const result = formatTimestamp(timestamp);
          expect(result).toBe(`${daysAgo}d ago`);
        }),
        { numRuns: 50 }
      );
    });

    it('should return formatted date for timestamps 7+ days ago', () => {
      fc.assert(
        fc.property(fc.integer({ min: 7, max: 365 }), (daysAgo) => {
          const date = new Date(fixedNow.getTime() - daysAgo * MS_PER_DAY);
          const timestamp = date.toISOString();
          const result = formatTimestamp(timestamp);

          // Property: Result SHALL be a locale date string
          expect(result).toBe(date.toLocaleDateString());
        }),
        { numRuns: 50 }
      );
    });

    it('should handle valid ISO timestamps correctly', () => {
      fc.assert(
        fc.property(
          fc.date({ min: new Date('2020-01-01'), max: fixedNow }).filter((d) => !isNaN(d.getTime())),
          (date) => {
            const timestamp = date.toISOString();
            const result = formatTimestamp(timestamp);

            // Property: Result SHALL be a non-empty string for valid dates
            expect(result.length).toBeGreaterThan(0);
          }
        ),
        { numRuns: 100 }
      );
    });
  });
});
