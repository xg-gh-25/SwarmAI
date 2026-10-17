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
import { groupSessionsByTime, formatTimestamp, mergeOlderMessages, resolvePendingToolUseId } from './utils';
import type { PendingQuestion } from './types';
import { MS_PER_DAY } from './constants';
import type { Message, ContentBlock } from '../../types';

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
   * For any valid timestamp, formatTimestamp SHALL return an absolute LOCAL
   * time string `YYYY-MM-DD HH:MM` (24-hour, zero-padded, no timezone
   * conversion, no relative "Xh ago"). Changed 2026-08-02 (XG): History is a
   * scan surface — an absolute, sortable stamp beats a drifting relative one.
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

    /** Reference formatter: the exact absolute-local shape the SUT must produce. */
    const expectedLocal = (date: Date): string => {
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
        `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    };

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

    it('should match the absolute-local YYYY-MM-DD HH:MM shape (regex)', () => {
      fc.assert(
        fc.property(
          fc.date({ min: new Date('2020-01-01'), max: fixedNow }).filter((d) => !isNaN(d.getTime())),
          (date) => {
            const result = formatTimestamp(date.toISOString());
            // Property: strict absolute shape, never a relative token.
            expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
            expect(result).not.toMatch(/ago|Just now/i);
          }
        ),
        { numRuns: 100 }
      );
    });

    it('should equal the local getters for any valid timestamp', () => {
      fc.assert(
        fc.property(
          fc.date({ min: new Date('2020-01-01'), max: new Date('2030-12-31') })
            .filter((d) => !isNaN(d.getTime())),
          (date) => {
            const result = formatTimestamp(date.toISOString());
            expect(result).toBe(expectedLocal(date));
          }
        ),
        { numRuns: 100 }
      );
    });

    it('does NOT drift with the current clock (absolute, not relative)', () => {
      const ts = new Date('2024-11-03T09:07:00.000Z').toISOString();
      const first = formatTimestamp(ts);
      // Advance the wall clock a year — an absolute stamp is unchanged.
      vi.setSystemTime(new Date('2026-02-19T12:00:00.000Z'));
      const later = formatTimestamp(ts);
      expect(later).toBe(first);
      expect(first).toBe(expectedLocal(new Date(ts)));
    });
  });
});

// ============== mergeOlderMessages (pagination seam) ==============

describe('mergeOlderMessages', () => {
  const asst = (id: string, text: string, model?: string): Message => ({
    id,
    role: 'assistant',
    content: [{ type: 'text', text } as ContentBlock],
    timestamp: id,
    model,
  });
  const user = (id: string, text: string): Message => ({
    id,
    role: 'user',
    content: [{ type: 'text', text } as ContentBlock],
    timestamp: id,
  });

  it('returns current when older is empty', () => {
    const cur = [asst('a1', 'hi')];
    expect(mergeOlderMessages([], cur)).toBe(cur);
  });

  it('returns older when current is empty', () => {
    const old = [asst('a1', 'hi')];
    expect(mergeOlderMessages(old, [])).toBe(old);
  });

  it('merges boundary when both sides are assistant', () => {
    // older page ends with assistant turn 1; current page starts with turn 2
    const older = [user('u1', 'q'), asst('a1', 'part one', 'sonnet')];
    const current = [asst('a2', ' part two', 'opus'), user('u2', 'q2')];
    const result = mergeOlderMessages(older, current);

    // u1, merged-assistant, u2 → 3 messages (a1+a2 collapsed)
    expect(result).toHaveLength(3);
    expect(result[0].id).toBe('u1');
    expect(result[1].id).toBe('a1'); // older anchor id retained
    expect(result[1].content.map((b) => (b as { text: string }).text)).toEqual([
      'part one',
      ' part two',
    ]);
    expect(result[1].model).toBe('opus'); // newer model wins
    expect(result[2].id).toBe('u2');
  });

  it('does NOT merge when boundary is user → assistant', () => {
    const older = [asst('a1', 'resp'), user('u1', 'next q')];
    const current = [asst('a2', 'resp2')];
    const result = mergeOlderMessages(older, current);
    expect(result).toHaveLength(3);
    expect(result.map((m) => m.id)).toEqual(['a1', 'u1', 'a2']);
  });

  it('does NOT merge when boundary is assistant → user', () => {
    const older = [asst('a1', 'resp')];
    const current = [user('u1', 'q'), asst('a2', 'resp2')];
    const result = mergeOlderMessages(older, current);
    expect(result).toHaveLength(3);
    expect(result.map((m) => m.id)).toEqual(['a1', 'u1', 'a2']);
  });

  it('does not mutate the input arrays or messages', () => {
    const olderMsg = asst('a1', 'one');
    const olderContent = olderMsg.content;
    const older = [olderMsg];
    const current = [asst('a2', 'two')];
    mergeOlderMessages(older, current);
    // Original content array length unchanged (no in-place concat)
    expect(olderMsg.content).toBe(olderContent);
    expect(olderMsg.content).toHaveLength(1);
    expect(older).toHaveLength(1);
  });
});

// ============== resolvePendingToolUseId (Root 3 / 3A — AskUserQuestion surfacing) ==============
//
// The bug: ChatPage sourced pendingToolUseId ONLY from React `pendingQuestion`
// state, which is null on background tabs / mid-switch (setPendingQuestion gated
// by isActiveTab in useChatStreamingLifecycle.ts:2001). The per-tab cache
// (tabState.pendingQuestion) IS populated regardless. resolvePendingToolUseId
// derives the renderer prop from the cache fallback so the question is answerable.
//
// PIT71 guard: it must source ONLY from the ACTIVE tab's cache — never another
// tab's — because ChatPage renders only the active tab's messages.
describe('resolvePendingToolUseId (Root 3 / 3A)', () => {
  const q = (id: string): PendingQuestion => ({ toolUseId: id, questions: [] });

  it('AC2: React state null but active-tab cache populated → returns cache toolUseId (form stays enabled)', () => {
    expect(resolvePendingToolUseId(null, q('tu-cache-1'))).toBe('tu-cache-1');
  });

  it('prefers React state when present (live same-tab case)', () => {
    expect(resolvePendingToolUseId(q('tu-react'), q('tu-cache'))).toBe('tu-react');
  });

  it('returns undefined when neither React state nor cache has a question', () => {
    expect(resolvePendingToolUseId(null, null)).toBeUndefined();
  });

  it('AC5 (PIT71 guard): only the ACTIVE tab cache is passed in — a non-active tab question is never the source', () => {
    // Caller passes ONLY the active tab's cache. Simulate active tab having no
    // question while a (different) background tab does: the background question
    // is simply not an argument, so it can never leak.
    const activeTabCache = null; // active tab has no pending question
    expect(resolvePendingToolUseId(null, activeTabCache)).toBeUndefined();
  });

  it('cache with empty toolUseId is treated as no question (undefined, not "")', () => {
    expect(resolvePendingToolUseId(null, { toolUseId: '', questions: [] })).toBeUndefined();
  });
});
