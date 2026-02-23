/**
 * Unit tests for useViewScope hook pure functions.
 *
 * Tests the view/scope toggle logic for workspace navigation:
 * - SwarmWS defaults to 'global', custom workspaces default to 'scoped'
 * - localStorage persistence for SwarmWS scope selection
 * - Effective workspace ID computation
 *
 * Validates: Requirements 37.1-37.12
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import {
  readPersistedScope,
  persistScope,
  getDefaultScope,
  resolveScope,
  getEffectiveWorkspaceId,
} from '../useViewScope';
import { getRecommendedItems } from '../../components/workspace-explorer/RecommendedGroup';
import type { SectionCounts } from '../../types/section';

// ============== localStorage mock ==============

class MockLocalStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null { return this.store.get(key) ?? null; }
  setItem(key: string, value: string): void { this.store.set(key, value); }
  removeItem(key: string): void { this.store.delete(key); }
  clear(): void { this.store.clear(); }
  get length(): number { return this.store.size; }
  key(_index: number): string | null { return null; }
}

let originalLocalStorage: Storage;

beforeEach(() => {
  originalLocalStorage = globalThis.localStorage;
  Object.defineProperty(globalThis, 'localStorage', {
    value: new MockLocalStorage(),
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  Object.defineProperty(globalThis, 'localStorage', {
    value: originalLocalStorage,
    writable: true,
    configurable: true,
  });
});

// ============== Tests ==============

describe('useViewScope - Pure Functions', () => {
  describe('getDefaultScope', () => {
    /**
     * Validates: Requirement 37.1 - SwarmWS defaults to Global View
     */
    it('returns "global" for default (SwarmWS) workspace', () => {
      expect(getDefaultScope(true)).toBe('global');
    });

    /**
     * Validates: Requirement 37.6 - Custom workspaces default to scoped view
     */
    it('returns "scoped" for custom workspaces', () => {
      expect(getDefaultScope(false)).toBe('scoped');
    });
  });

  describe('persistScope / readPersistedScope', () => {
    /**
     * Validates: Requirement 37.4 - Persist selection across sessions
     */
    it('persists and reads scope from localStorage', () => {
      persistScope('scoped');
      expect(readPersistedScope()).toBe('scoped');

      persistScope('global');
      expect(readPersistedScope()).toBe('global');
    });

    it('returns null when no persisted value', () => {
      expect(readPersistedScope()).toBeNull();
    });

    it('returns null for invalid persisted value', () => {
      localStorage.setItem('swarm-view-scope', 'invalid');
      expect(readPersistedScope()).toBeNull();
    });
  });

  describe('resolveScope', () => {
    /**
     * Validates: Requirement 37.1, 37.3 - SwarmWS defaults to global, uses persisted
     */
    it('uses persisted value for SwarmWS when available', () => {
      expect(resolveScope(true, 'scoped')).toBe('scoped');
      expect(resolveScope(true, 'global')).toBe('global');
    });

    it('defaults to global for SwarmWS when no persisted value', () => {
      expect(resolveScope(true, null)).toBe('global');
    });

    /**
     * Validates: Requirement 37.6 - Custom workspaces always default to scoped
     */
    it('always defaults to scoped for custom workspaces', () => {
      expect(resolveScope(false, null)).toBe('scoped');
      expect(resolveScope(false, 'global')).toBe('scoped');
      expect(resolveScope(false, 'scoped')).toBe('scoped');
    });
  });

  describe('getEffectiveWorkspaceId', () => {
    /**
     * Validates: Requirement 37.9 - Global view uses workspace_id="all"
     */
    it('returns "all" for global scope', () => {
      expect(getEffectiveWorkspaceId('global', 'ws-123')).toBe('all');
    });

    /**
     * Validates: Requirement 37.10 - Scoped view uses actual workspace ID
     */
    it('returns actual workspace ID for scoped view', () => {
      expect(getEffectiveWorkspaceId('scoped', 'ws-123')).toBe('ws-123');
    });
  });
});

describe('getRecommendedItems', () => {
  const emptyCounts: SectionCounts = {
    signals: { total: 0, pending: 0, overdue: 0, inDiscussion: 0 },
    plan: { total: 0, today: 0, upcoming: 0, blocked: 0 },
    execute: { total: 0, draft: 0, wip: 0, blocked: 0, completed: 0 },
    communicate: { total: 0, pendingReply: 0, aiDraft: 0, followUp: 0 },
    artifacts: { total: 0, plan: 0, report: 0, doc: 0, decision: 0 },
    reflection: { total: 0, dailyRecap: 0, weeklySummary: 0, lessonsLearned: 0 },
  };

  it('returns empty array when all counts are zero', () => {
    expect(getRecommendedItems(emptyCounts)).toEqual([]);
  });

  it('returns top N items sorted by count descending', () => {
    const counts: SectionCounts = {
      ...emptyCounts,
      signals: { total: 5, pending: 3, overdue: 2, inDiscussion: 0 },
      execute: { total: 10, draft: 1, wip: 5, blocked: 4, completed: 0 },
      communicate: { total: 1, pendingReply: 1, aiDraft: 0, followUp: 0 },
    };

    const items = getRecommendedItems(counts, 3);
    expect(items).toHaveLength(3);
    // Should be sorted by count desc: wip(5) > blocked(4) > pending(3)
    expect(items[0].count).toBeGreaterThanOrEqual(items[1].count);
    expect(items[1].count).toBeGreaterThanOrEqual(items[2].count);
  });

  it('respects topN parameter', () => {
    const counts: SectionCounts = {
      ...emptyCounts,
      signals: { total: 10, pending: 5, overdue: 5, inDiscussion: 0 },
      plan: { total: 3, today: 3, upcoming: 0, blocked: 0 },
      execute: { total: 2, draft: 0, wip: 2, blocked: 0, completed: 0 },
    };

    expect(getRecommendedItems(counts, 2)).toHaveLength(2);
    expect(getRecommendedItems(counts, 1)).toHaveLength(1);
  });
});
