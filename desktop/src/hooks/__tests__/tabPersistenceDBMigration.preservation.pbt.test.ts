/**
 * Preservation property tests for tab persistence DB migration.
 *
 * Testing methodology: property-based testing (fast-check) + vitest.
 *
 * What is being tested:
 *   The `useUnifiedTabState` hook's EXISTING behavior that must remain
 *   unchanged after the file-based persistence fix is applied. These tests
 *   verify that runtime tab operations (add, close, switch, metadata
 *   updates) work correctly regardless of the persistence backend.
 *
 * Key properties verified:
 *   - Property 2a: Hook initializes with a single default tab (file
 *     restore is async and tested separately).
 *   - Property 2b: Runtime tab operations — add, close, switch preserve
 *     invariants (tab count, valid activeTabId, MAX_OPEN_TABS enforcement).
 *   - Property 2c: Metadata updates — title, sessionId changes are
 *     reflected in the openTabs derived view correctly.
 *
 * EXPECTED OUTCOME: All tests PASS (confirms baseline behavior preserved).
 *
 * Validates: Requirements 2.6, 3.1, 3.2, 3.3, 3.5, 3.6, 3.7
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as fc from 'fast-check';
import { renderHook, act } from '@testing-library/react';
import {
  useUnifiedTabState,
  MAX_OPEN_TABS,
} from '../useUnifiedTabState';

// Mock the tabPersistence service (file-based persistence is async)
vi.mock('../../services/tabPersistence', () => ({
  tabPersistenceService: {
    load: vi.fn().mockResolvedValue(null),
    save: vi.fn().mockResolvedValue(undefined),
  },
}));

const DEFAULT_AGENT = 'test-agent';

// ---------------------------------------------------------------------------
// fast-check arbitraries
// ---------------------------------------------------------------------------

type RuntimeOp =
  | { kind: 'addTab' }
  | { kind: 'closeTab'; index: number }
  | { kind: 'selectTab'; index: number };

const runtimeOpArb: fc.Arbitrary<RuntimeOp> = fc.oneof(
  fc.constant({ kind: 'addTab' as const }),
  fc.record({
    kind: fc.constant('closeTab' as const),
    index: fc.nat({ max: 9 }),
  }),
  fc.record({
    kind: fc.constant('selectTab' as const),
    index: fc.nat({ max: 9 }),
  }),
);

function applyRuntimeOp(
  r: ReturnType<typeof useUnifiedTabState>,
  op: RuntimeOp,
): void {
  const tabs = r.openTabs;
  switch (op.kind) {
    case 'addTab':
      r.addTab(DEFAULT_AGENT);
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
  }
}

function assertInvariants(r: ReturnType<typeof useUnifiedTabState>) {
  expect(r.openTabs.length).toBeGreaterThanOrEqual(1);
  expect(r.openTabs.some((t) => t.id === r.activeTabId)).toBe(true);
  const ids = r.openTabs.map((t) => t.id);
  expect(new Set(ids).size).toBe(ids.length);
  expect(r.openTabs.length).toBeLessThanOrEqual(MAX_OPEN_TABS);
}


// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Preservation Property Tests — Property 2', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /**
   * Property 2a: Hook always initializes with a single default tab.
   * File restore is async (called by ChatPage), so the hook starts clean.
   */
  it('Property 2a: hook initializes with a single default tab', () => {
    const { result } = renderHook(() =>
      useUnifiedTabState(DEFAULT_AGENT),
    );

    expect(result.current.openTabs.length).toBe(1);
    expect(result.current.openTabs[0].title).toBe('New Session');
    expect(result.current.openTabs[0].agentId).toBe(DEFAULT_AGENT);
    expect(result.current.openTabs[0].isNew).toBe(true);
    expect(result.current.openTabs[0].sessionId).toBeUndefined();
    expect(result.current.activeTabId).toBe(
      result.current.openTabs[0].id,
    );
  });

  /**
   * Property 2b: Runtime Tab Operations Unchanged
   *
   * For all sequences of runtime tab operations (add, close, switch),
   * the hook preserves invariants: tab count >= 1, <= MAX_OPEN_TABS,
   * activeTabId always points to a valid tab, unique IDs.
   */
  it('Property 2b: runtime tab operations preserve invariants', () => {
    fc.assert(
      fc.property(
        fc.array(runtimeOpArb, { minLength: 1, maxLength: 15 }),
        (ops) => {
          const { result } = renderHook(() =>
            useUnifiedTabState(DEFAULT_AGENT),
          );

          assertInvariants(result.current);

          for (const op of ops) {
            act(() => applyRuntimeOp(result.current, op));
            assertInvariants(result.current);
          }

          // addTab at MAX returns undefined
          if (result.current.openTabs.length >= MAX_OPEN_TABS) {
            let overflow: ReturnType<typeof result.current.addTab>;
            act(() => {
              overflow = result.current.addTab(DEFAULT_AGENT);
            });
            expect(overflow!).toBeUndefined();
          }
        },
      ),
      { numRuns: 50 },
    );
  });


  /**
   * Property 2c: Metadata Updates Reflected in openTabs View
   *
   * For all tab metadata updates (title, sessionId), the openTabs
   * derived view reflects the changes correctly. Runtime fields
   * (messages, isStreaming, etc.) are excluded from openTabs.
   */
  it('Property 2c: metadata updates reflected in openTabs view', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 30 }),
        fc.uuid(),
        (newTitle, newSessionId) => {
          const { result } = renderHook(() =>
            useUnifiedTabState(DEFAULT_AGENT),
          );

          const targetId = result.current.openTabs[0].id;

          act(() => {
            result.current.updateTabTitle(targetId, newTitle);
          });
          act(() => {
            result.current.updateTabSessionId(targetId, newSessionId);
          });

          // Verify openTabs reflects the updates
          const tab = result.current.openTabs.find(
            (t) => t.id === targetId,
          );
          expect(tab).toBeDefined();
          expect(tab!.title).toBe(newTitle);
          expect(tab!.sessionId).toBe(newSessionId);

          // Verify no runtime fields in openTabs
          for (const entry of result.current.openTabs) {
            const raw = entry as Record<string, unknown>;
            expect(raw.messages).toBeUndefined();
            expect(raw.pendingQuestion).toBeUndefined();
            expect(raw.isStreaming).toBeUndefined();
            expect(raw.abortController).toBeUndefined();
            expect(raw.streamGen).toBeUndefined();
            expect(raw.status).toBeUndefined();
          }
        },
      ),
      { numRuns: 50 },
    );
  });
});
