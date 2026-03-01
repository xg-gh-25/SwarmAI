/**
 * Property-based and unit tests for the useUnifiedTabState hook.
 *
 * Testing methodology: property-based tests (fast-check) + unit tests (vitest).
 *
 * Key properties verified:
 * - Property 1: Tab Operation Invariants (≥1 tab, valid activeTabId, unique ids, ≤MAX)
 * - Property 2: Per-Tab State Isolation (patching tab A leaves tab B unchanged)
 * - Property 3: addTab Produces Correct Defaults
 * - Property 4: closeTab Removes and Reselects
 * - Property 5: Metadata Updates Apply to Correct Tab
 * - Property 6: localStorage Persistence Round-Trip
 * - Property 7: removeInvalidTabs Resets Stale Tabs
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import {
  useUnifiedTabState,
  MAX_OPEN_TABS,
} from '../useUnifiedTabState';
import type { UnifiedTab, TabStatus, SerializableTab } from '../useUnifiedTabState';
import {
  OPEN_TABS_STORAGE_KEY,
  ACTIVE_TAB_STORAGE_KEY,
} from '../../pages/chat/constants';

// ---------------------------------------------------------------------------
// localStorage mock
// ---------------------------------------------------------------------------

class MockLocalStorage {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  clear(): void {
    this.store.clear();
  }
  get length(): number {
    return this.store.size;
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
}


let mockStorage: MockLocalStorage;

beforeEach(() => {
  mockStorage = new MockLocalStorage();
  vi.stubGlobal('localStorage', mockStorage);
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DEFAULT_AGENT = 'test-agent';

/** Render the hook with a fresh localStorage. */
function renderUnifiedHook(agentId = DEFAULT_AGENT) {
  return renderHook(() => useUnifiedTabState(agentId));
}

/** Assert the four core invariants on the hook result. */
function assertInvariants(r: ReturnType<typeof useUnifiedTabState>) {
  // Invariant 1: at least one tab
  expect(r.openTabs.length).toBeGreaterThanOrEqual(1);
  // Invariant 2: activeTabId references an existing tab
  expect(r.openTabs.some((t) => t.id === r.activeTabId)).toBe(true);
  // Invariant 3: unique ids
  const ids = r.openTabs.map((t) => t.id);
  expect(new Set(ids).size).toBe(ids.length);
  // Invariant 4: tab count ≤ MAX_OPEN_TABS
  expect(r.openTabs.length).toBeLessThanOrEqual(MAX_OPEN_TABS);
}

// ---------------------------------------------------------------------------
// fast-check arbitraries for tab operations
// ---------------------------------------------------------------------------

const tabStatusArb: fc.Arbitrary<TabStatus> = fc.constantFrom(
  'idle', 'streaming', 'waiting_input',
  'permission_needed', 'error', 'complete_unread',
);

type OpType =
  | { kind: 'addTab'; agentId: string }
  | { kind: 'closeTab'; index: number }
  | { kind: 'selectTab'; index: number }
  | { kind: 'updateTitle'; index: number; title: string }
  | { kind: 'updateSessionId'; index: number; sessionId: string }
  | { kind: 'setIsNew'; index: number; isNew: boolean }
  | { kind: 'updateTabState'; index: number; status: TabStatus }
  | { kind: 'updateTabStatus'; index: number; status: TabStatus }
  | { kind: 'removeInvalidTabs' };

const opArb: fc.Arbitrary<OpType> = fc.oneof(
  fc.record({ kind: fc.constant('addTab' as const), agentId: fc.string({ minLength: 1, maxLength: 10 }) }),
  fc.record({ kind: fc.constant('closeTab' as const), index: fc.nat({ max: 9 }) }),
  fc.record({ kind: fc.constant('selectTab' as const), index: fc.nat({ max: 9 }) }),
  fc.record({ kind: fc.constant('updateTitle' as const), index: fc.nat({ max: 9 }), title: fc.string({ minLength: 1, maxLength: 20 }) }),
  fc.record({ kind: fc.constant('updateSessionId' as const), index: fc.nat({ max: 9 }), sessionId: fc.uuid() }),
  fc.record({ kind: fc.constant('setIsNew' as const), index: fc.nat({ max: 9 }), isNew: fc.boolean() }),
  fc.record({ kind: fc.constant('updateTabState' as const), index: fc.nat({ max: 9 }), status: tabStatusArb }),
  fc.record({ kind: fc.constant('updateTabStatus' as const), index: fc.nat({ max: 9 }), status: tabStatusArb }),
  fc.record({ kind: fc.constant('removeInvalidTabs' as const) }),
);

/** Apply a single operation to the hook result, clamping indices to valid range. */
function applyOp(r: ReturnType<typeof useUnifiedTabState>, op: OpType) {
  const tabs = r.openTabs;
  switch (op.kind) {
    case 'addTab':
      r.addTab(op.agentId);
      break;
    case 'closeTab': {
      const idx = op.index % tabs.length;
      r.closeTab(tabs[idx].id);
      break;
    }
    case 'selectTab': {
      const idx = op.index % tabs.length;
      r.selectTab(tabs[idx].id);
      break;
    }
    case 'updateTitle': {
      const idx = op.index % tabs.length;
      r.updateTabTitle(tabs[idx].id, op.title);
      break;
    }
    case 'updateSessionId': {
      const idx = op.index % tabs.length;
      r.updateTabSessionId(tabs[idx].id, op.sessionId);
      break;
    }
    case 'setIsNew': {
      const idx = op.index % tabs.length;
      r.setTabIsNew(tabs[idx].id, op.isNew);
      break;
    }
    case 'updateTabState': {
      const idx = op.index % tabs.length;
      r.updateTabState(tabs[idx].id, { status: op.status });
      break;
    }
    case 'updateTabStatus': {
      const idx = op.index % tabs.length;
      r.updateTabStatus(tabs[idx].id, op.status);
      break;
    }
    case 'removeInvalidTabs':
      r.removeInvalidTabs(new Set<string>());
      break;
  }
}

// ===========================================================================
// Property-Based Tests
// ===========================================================================

describe('Property-Based Tests', () => {
  // Feature: unified-tab-state, Property 1: Tab Operation Invariants
  // Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 2.3, 2.7
  it('Property 1: Tab Operation Invariants — random op sequences preserve all four invariants', () => {
    fc.assert(
      fc.property(
        fc.array(opArb, { minLength: 1, maxLength: 20 }),
        (ops) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Invariants hold on initial state
          assertInvariants(result.current);

          for (const op of ops) {
            act(() => applyOp(result.current, op));
            assertInvariants(result.current);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 2: Per-Tab State Isolation
  // Validates: Requirements 8.1, 8.2, 8.3
  it('Property 2: Per-Tab State Isolation — patching tab A leaves tab B unchanged', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 10 }),
        tabStatusArb,
        fc.boolean(),
        (patchTitle, patchStatus, useStatusUpdate) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Add a second tab so we have at least 2
          act(() => { result.current.addTab('agent-b'); });

          const tabs = result.current.openTabs;
          expect(tabs.length).toBeGreaterThanOrEqual(2);

          const tabA = tabs[0];
          const tabB = tabs[1];

          // Snapshot tab B before mutation
          const bBefore = result.current.getTabState(tabB.id);
          expect(bBefore).toBeDefined();
          const bSnapshot = { ...bBefore! };
          const bMsgRef = bBefore!.messages;

          // Mutate tab A
          act(() => {
            if (useStatusUpdate) {
              result.current.updateTabStatus(tabA.id, patchStatus);
            } else {
              result.current.updateTabState(tabA.id, {
                title: patchTitle,
                status: patchStatus,
              });
            }
          });

          // Verify tab B is unchanged
          const bAfter = result.current.getTabState(tabB.id);
          expect(bAfter).toBeDefined();
          expect(bAfter!.title).toBe(bSnapshot.title);
          expect(bAfter!.status).toBe(bSnapshot.status);
          expect(bAfter!.isStreaming).toBe(bSnapshot.isStreaming);
          expect(bAfter!.messages).toBe(bMsgRef);
          expect(bAfter!.pendingQuestion).toBe(bSnapshot.pendingQuestion);
          expect(bAfter!.streamGen).toBe(bSnapshot.streamGen);
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 3: addTab Produces Correct Defaults
  // Validates: Requirements 2.1, 2.2
  it('Property 3: addTab Produces Correct Defaults — new tab has all expected defaults', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 30 }),
        (agentId) => {
          // Clear localStorage so each iteration starts fresh with 1 default tab
          mockStorage.clear();
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          expect(result.current.openTabs.length).toBe(1);
          act(() => { result.current.addTab(agentId); });

          // A new tab was added
          const tabs = result.current.openTabs;
          expect(tabs.length).toBe(2);

          // The new tab is the last one (addTab appends)
          const newTab = tabs[tabs.length - 1];
          expect(newTab.id).toBeTruthy();
          expect(newTab.title).toBe('New Session');
          expect(newTab.agentId).toBe(agentId);
          expect(newTab.isNew).toBe(true);
          expect(newTab.sessionId).toBeUndefined();

          // Verify runtime defaults via getTabState
          const full = result.current.getTabState(newTab.id);
          expect(full).toBeDefined();
          expect(full!.messages).toEqual([]);
          expect(full!.pendingQuestion).toBeNull();
          expect(full!.isStreaming).toBe(false);
          expect(full!.abortController).toBeNull();
          expect(full!.streamGen).toBe(0);
          expect(full!.status).toBe('idle');

          // activeTabId should be the new tab
          expect(result.current.activeTabId).toBe(newTab.id);
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 4: closeTab Removes and Reselects
  // Validates: Requirements 2.4, 2.5, 2.6
  it('Property 4: closeTab Removes and Reselects — removal, reselection, and abort', () => {
    fc.assert(
      fc.property(
        fc.nat({ max: 4 }),
        fc.boolean(),
        (extraTabs, closeActive) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Add extra tabs so we have 2+
          act(() => { result.current.addTab('agent-extra'); });
          for (let i = 0; i < extraTabs; i++) {
            act(() => { result.current.addTab(`agent-${i}`); });
          }

          const tabsBefore = result.current.openTabs;
          const countBefore = tabsBefore.length;
          expect(countBefore).toBeGreaterThanOrEqual(2);

          // Pick which tab to close
          const targetIdx = closeActive
            ? tabsBefore.findIndex((t) => t.id === result.current.activeTabId)
            : tabsBefore.findIndex((t) => t.id !== result.current.activeTabId);
          const targetId = tabsBefore[targetIdx].id;

          // Set up a mock abort controller on the target
          const abortSpy = vi.fn();
          const mockController = { abort: abortSpy, signal: new AbortController().signal } as unknown as AbortController;
          act(() => {
            result.current.updateTabState(targetId, { abortController: mockController });
          });

          act(() => { result.current.closeTab(targetId); });

          // Tab is removed
          expect(result.current.openTabs.find((t) => t.id === targetId)).toBeUndefined();
          expect(result.current.openTabs.length).toBe(countBefore - 1);

          // abort was called
          expect(abortSpy).toHaveBeenCalled();

          // activeTabId still valid
          expect(result.current.openTabs.some((t) => t.id === result.current.activeTabId)).toBe(true);
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 5: Metadata Updates Apply to Correct Tab
  // Validates: Requirements 3.1, 3.2, 3.3, 3.4
  it('Property 5: Metadata Updates — only target tab is changed, non-existent id is no-op', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 20 }),
        fc.uuid(),
        fc.boolean(),
        (title, sessionId, isNew) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Add a second tab
          act(() => { result.current.addTab('agent-2'); });
          const tabs = result.current.openTabs;
          const target = tabs[0];
          const other = tabs[1];

          // Snapshot other tab
          const otherBefore = { ...result.current.getTabState(other.id)! };

          // Apply metadata updates to target
          act(() => {
            result.current.updateTabTitle(target.id, title);
            result.current.updateTabSessionId(target.id, sessionId);
            result.current.setTabIsNew(target.id, isNew);
          });

          // Verify target changed
          const t = result.current.getTabState(target.id)!;
          expect(t.title).toBe(title);
          expect(t.sessionId).toBe(sessionId);
          expect(t.isNew).toBe(isNew);

          // Verify other unchanged
          const o = result.current.getTabState(other.id)!;
          expect(o.title).toBe(otherBefore.title);
          expect(o.sessionId).toBe(otherBefore.sessionId);
          expect(o.isNew).toBe(otherBefore.isNew);

          // Non-existent tabId is a no-op (no throw)
          const countBefore = result.current.openTabs.length;
          act(() => {
            result.current.updateTabTitle('nonexistent', 'x');
            result.current.updateTabSessionId('nonexistent', 'x');
            result.current.setTabIsNew('nonexistent', true);
          });
          expect(result.current.openTabs.length).toBe(countBefore);
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 6: localStorage Persistence Round-Trip
  // Validates: Requirements 6.1, 6.2, 6.4, 6.6
  it('Property 6: localStorage Persistence Round-Trip — serializable subset persists correctly', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            title: fc.string({ minLength: 1, maxLength: 20 }),
            agentId: fc.string({ minLength: 1, maxLength: 10 }),
            sessionId: fc.option(fc.uuid(), { nil: undefined }),
          }),
          { minLength: 1, maxLength: 5 },
        ),
        (tabConfigs) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Apply metadata mutations to create the desired state
          for (const cfg of tabConfigs) {
            act(() => { result.current.addTab(cfg.agentId); });
          }

          // Update titles and sessionIds
          const tabs = result.current.openTabs;
          for (let i = 0; i < Math.min(tabConfigs.length, tabs.length); i++) {
            const cfg = tabConfigs[i];
            act(() => {
              result.current.updateTabTitle(tabs[i].id, cfg.title);
              if (cfg.sessionId) {
                result.current.updateTabSessionId(tabs[i].id, cfg.sessionId);
              }
            });
          }

          // Read back from localStorage
          const raw = mockStorage.getItem(OPEN_TABS_STORAGE_KEY);
          expect(raw).toBeTruthy();
          const parsed: SerializableTab[] = JSON.parse(raw!);

          // Verify it's an array of serializable tabs
          expect(Array.isArray(parsed)).toBe(true);
          for (const entry of parsed) {
            // Must have serializable fields
            expect(typeof entry.id).toBe('string');
            expect(typeof entry.title).toBe('string');
            expect(typeof entry.agentId).toBe('string');
            expect(typeof entry.isNew).toBe('boolean');
            // Must NOT have runtime fields
            expect((entry as Record<string, unknown>).messages).toBeUndefined();
            expect((entry as Record<string, unknown>).pendingQuestion).toBeUndefined();
            expect((entry as Record<string, unknown>).isStreaming).toBeUndefined();
            expect((entry as Record<string, unknown>).abortController).toBeUndefined();
            expect((entry as Record<string, unknown>).streamGen).toBeUndefined();
            expect((entry as Record<string, unknown>).status).toBeUndefined();
          }

          // activeTabId persisted
          const activeRaw = mockStorage.getItem(ACTIVE_TAB_STORAGE_KEY);
          expect(activeRaw).toBe(result.current.activeTabId);
        },
      ),
      { numRuns: 100 },
    );
  });

  // Feature: unified-tab-state, Property 7: removeInvalidTabs Resets Stale Tabs
  // Validates: Requirements 7.1, 7.2
  it('Property 7: removeInvalidTabs — stale tabs reset, valid tabs unchanged', () => {
    fc.assert(
      fc.property(
        fc.array(fc.option(fc.uuid(), { nil: undefined }), { minLength: 2, maxLength: 5 }),
        (sessionIds) => {
          const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

          // Create tabs and assign sessionIds
          for (let i = 1; i < sessionIds.length; i++) {
            act(() => { result.current.addTab(`agent-${i}`); });
          }

          const tabs = result.current.openTabs;
          for (let i = 0; i < Math.min(sessionIds.length, tabs.length); i++) {
            const sid = sessionIds[i];
            if (sid) {
              act(() => { result.current.updateTabSessionId(tabs[i].id, sid); });
            }
          }

          // Build valid set: pick a random subset of assigned sessionIds
          const assigned = sessionIds.filter((s): s is string => s !== undefined);
          const validSet = new Set(assigned.slice(0, Math.floor(assigned.length / 2)));

          // Snapshot tabs before
          const before = result.current.openTabs.map((t) => ({
            id: t.id,
            sessionId: t.sessionId,
            title: t.title,
            isNew: t.isNew,
          }));

          act(() => { result.current.removeInvalidTabs(validSet); });

          // Verify each tab
          for (const b of before) {
            const after = result.current.getTabState(b.id);
            expect(after).toBeDefined();

            if (b.sessionId && !validSet.has(b.sessionId)) {
              // Stale tab: should be reset
              expect(after!.sessionId).toBeUndefined();
              expect(after!.isNew).toBe(true);
              expect(after!.title).toBe('New Session');
            } else if (!b.sessionId || validSet.has(b.sessionId)) {
              // Valid or no-session tab: unchanged
              expect(after!.sessionId).toBe(b.sessionId);
              expect(after!.title).toBe(b.title);
              expect(after!.isNew).toBe(b.isNew);
            }
          }
        },
      ),
      { numRuns: 100 },
    );
  });

}); // end Property-Based Tests

// ===========================================================================
// Unit Tests — Edge Cases
// ===========================================================================

describe('Unit Tests — Edge Cases', () => {
  // Validates: Requirement 2.3
  it('addTab at MAX_OPEN_TABS returns undefined', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    // Fill to MAX_OPEN_TABS (starts with 1)
    for (let i = 1; i < MAX_OPEN_TABS; i++) {
      act(() => { result.current.addTab(`agent-${i}`); });
    }
    expect(result.current.openTabs.length).toBe(MAX_OPEN_TABS);

    // Next addTab should return undefined
    let overflow: ReturnType<typeof result.current.addTab>;
    act(() => { overflow = result.current.addTab('overflow'); });
    expect(overflow!).toBeUndefined();
    expect(result.current.openTabs.length).toBe(MAX_OPEN_TABS);
  });

  // Validates: Requirement 2.7
  it('closeTab on last tab auto-creates new tab', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    expect(result.current.openTabs.length).toBe(1);
    const onlyTabId = result.current.openTabs[0].id;

    act(() => { result.current.closeTab(onlyTabId); });

    expect(result.current.openTabs.length).toBe(1);
    expect(result.current.openTabs[0].id).not.toBe(onlyTabId);
    expect(result.current.activeTabId).toBe(result.current.openTabs[0].id);
  });

  // Validates: Requirements 6.2, 6.3
  it('Initialization with empty localStorage creates default tab', () => {
    // localStorage is empty (fresh mockStorage)
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    expect(result.current.openTabs.length).toBe(1);
    expect(result.current.openTabs[0].title).toBe('New Session');
    expect(result.current.openTabs[0].agentId).toBe(DEFAULT_AGENT);
    expect(result.current.activeTabId).toBe(result.current.openTabs[0].id);
  });

  // Validates: Requirement 6.5
  it('Initialization with stale activeTabId falls back to first tab', () => {
    // Pre-populate localStorage with tabs but a stale activeTabId
    const tabs = [
      { id: 'tab-1', title: 'Tab 1', agentId: 'a1', isNew: false, sessionId: 's1' },
      { id: 'tab-2', title: 'Tab 2', agentId: 'a2', isNew: true },
    ];
    mockStorage.setItem(OPEN_TABS_STORAGE_KEY, JSON.stringify(tabs));
    mockStorage.setItem(ACTIVE_TAB_STORAGE_KEY, 'nonexistent-tab-id');

    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    expect(result.current.openTabs.length).toBe(2);
    // Should fall back to first tab
    expect(result.current.activeTabId).toBe('tab-1');
  });

  // Validates: Requirement 4.4
  it('updateTabState with non-existent tabId is a no-op', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    const tabsBefore = result.current.openTabs.map((t) => t.id);

    act(() => {
      result.current.updateTabState('nonexistent', { isStreaming: true });
    });

    // Nothing changed
    const tabsAfter = result.current.openTabs.map((t) => t.id);
    expect(tabsAfter).toEqual(tabsBefore);
  });

  // Validates: Requirement 7.3
  it('removeInvalidTabs with no invalid tabs does not trigger re-render', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    // Give the tab a sessionId that will be in the valid set
    const tabId = result.current.openTabs[0].id;
    act(() => { result.current.updateTabSessionId(tabId, 'valid-session'); });

    const titleBefore = result.current.openTabs[0].title;

    act(() => {
      result.current.removeInvalidTabs(new Set(['valid-session']));
    });

    // Tab should be completely unchanged
    expect(result.current.openTabs[0].title).toBe(titleBefore);
    expect(result.current.getTabState(tabId)!.sessionId).toBe('valid-session');
  });

  // Validates: Requirement 2.6
  it('closeTab aborts streaming tab controller', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    // Add a second tab so closing doesn't auto-create
    act(() => { result.current.addTab('agent-2'); });

    const tabId = result.current.openTabs[0].id;
    const abortSpy = vi.fn();
    const mockCtrl = { abort: abortSpy, signal: new AbortController().signal } as unknown as AbortController;

    act(() => {
      result.current.updateTabState(tabId, { abortController: mockCtrl });
    });

    act(() => { result.current.closeTab(tabId); });

    expect(abortSpy).toHaveBeenCalledTimes(1);
  });

  // Validates: Requirement 2.8
  it('selectTab updates activeTabId and derived activeTab', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    act(() => { result.current.addTab('agent-2'); });

    const tabs = result.current.openTabs;
    const firstId = tabs[0].id;
    const secondId = tabs[1].id;

    // Active should be the second (most recently added)
    expect(result.current.activeTabId).toBe(secondId);

    act(() => { result.current.selectTab(firstId); });

    expect(result.current.activeTabId).toBe(firstId);
    expect(result.current.activeTab?.id).toBe(firstId);
  });

  // Validates: Requirement 6.6
  it('Persistence excludes runtime fields from localStorage', () => {
    const { result } = renderHook(() => useUnifiedTabState(DEFAULT_AGENT));

    const tabId = result.current.openTabs[0].id;

    // Set runtime state
    act(() => {
      result.current.updateTabState(tabId, {
        isStreaming: true,
        status: 'streaming',
        streamGen: 5,
      });
    });

    const raw = mockStorage.getItem(OPEN_TABS_STORAGE_KEY);
    expect(raw).toBeTruthy();
    const parsed = JSON.parse(raw!);

    for (const entry of parsed) {
      expect(entry.messages).toBeUndefined();
      expect(entry.pendingQuestion).toBeUndefined();
      expect(entry.isStreaming).toBeUndefined();
      expect(entry.abortController).toBeUndefined();
      expect(entry.streamGen).toBeUndefined();
      expect(entry.status).toBeUndefined();
    }
  });
}); // end Unit Tests
