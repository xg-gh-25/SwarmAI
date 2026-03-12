/**
 * Shared test utilities for streaming lifecycle and multi-tab isolation tests.
 *
 * Provides common helpers used by both ``useChatStreamingLifecycle.test.ts``
 * and ``multiTabStreamingIsolation.pbt.test.ts`` to avoid duplication:
 *
 * - ``testTabMap`` / ``testTabMapRef`` / ``testActiveTabIdRef`` — shared mutable state
 * - ``createMockDeps``   — builds a ``ChatStreamingLifecycleDeps`` with vi.fn() mocks
 * - ``initTestTab``      — creates a UnifiedTab entry and sets it as active
 * - ``makeMessage``      — builds a Message with sensible defaults
 * - ``makeToolUse``      — builds a tool_use ContentBlock
 *
 * @module streamingTestUtils
 */

import { vi } from 'vitest';
import type { UnifiedTab, TabStatus } from '../../hooks/useUnifiedTabState';
import type { ChatStreamingLifecycleDeps } from '../../hooks/useChatStreamingLifecycle';
import type { Message, ContentBlock } from '../../types';
import React from 'react';

// ---------------------------------------------------------------------------
// Shared mutable state — tests can read/write these directly
// ---------------------------------------------------------------------------

export const testTabMap = new Map<string, UnifiedTab>();
export const testTabMapRef = { current: testTabMap };
export const testActiveTabIdRef = { current: null as string | null };


// ---------------------------------------------------------------------------
// Mock deps factory
// ---------------------------------------------------------------------------

/** Create mock deps for the streaming lifecycle hook. */
export function createMockDeps(): ChatStreamingLifecycleDeps {
  return {
    queryClient: { invalidateQueries: vi.fn() },
    getTabState: (tabId: string) => testTabMap.get(tabId),
    updateTabState: vi.fn((tabId: string, patch: Partial<Omit<UnifiedTab, 'id'>>) => {
      const tab = testTabMap.get(tabId);
      if (tab) Object.assign(tab, patch);
    }),
    updateTabStatus: vi.fn((tabId: string, status: TabStatus) => {
      const tab = testTabMap.get(tabId);
      if (tab) tab.status = status;
    }),
    tabMapRef: testTabMapRef as React.MutableRefObject<Map<string, UnifiedTab>>,
    activeTabIdRef: testActiveTabIdRef as React.MutableRefObject<string | null>,
  };
}

// ---------------------------------------------------------------------------
// Tab helpers
// ---------------------------------------------------------------------------

/** Create a UnifiedTab entry in the test map and set it as active. */
export function initTestTab(tabId: string, initialMessages?: Message[]): void {
  testTabMap.set(tabId, {
    id: tabId,
    title: 'New Session',
    agentId: 'default',
    isNew: true,
    sessionId: undefined,
    messages: initialMessages ?? [],
    pendingQuestion: null,
    isStreaming: false,
    abortController: null,
    streamGen: 0,
    status: 'idle' as TabStatus,
    contextWarning: null,
    isReconnecting: false,
    reconnectionAttempt: 0,
    hasReceivedData: false,
    attachments: [],
  });
  testActiveTabIdRef.current = tabId;
}

// ---------------------------------------------------------------------------
// Message / ContentBlock builders
// ---------------------------------------------------------------------------

/** Build a Message with sensible defaults. */
export function makeMessage(
  overrides: Partial<Message> & { role: Message['role'] },
): Message {
  const { role, id, content, timestamp, ...rest } = overrides;
  return {
    id: id ?? crypto.randomUUID(),
    role,
    content: content ?? [],
    timestamp: timestamp ?? new Date().toISOString(),
    ...rest,
  };
}

/** Build a tool_use ContentBlock. */
export function makeToolUse(name: string, id?: string): ContentBlock {
  return {
    type: 'tool_use' as const,
    id: id ?? crypto.randomUUID(),
    name,
    summary: 'Using tool',
  };
}

/** Reset shared test state — call in beforeEach. */
export function resetTestState(): void {
  testTabMap.clear();
  testActiveTabIdRef.current = null;
}
