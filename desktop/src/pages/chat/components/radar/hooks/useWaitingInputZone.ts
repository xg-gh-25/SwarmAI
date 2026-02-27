/**
 * React hook and pure derivation functions for the Waiting Input zone.
 *
 * Derives ``RadarWaitingItem[]`` from SSE props (``pendingQuestion`` and
 * ``pendingPermission``) passed from ChatPage to SwarmRadar. All derivation
 * is pure — no API calls, no DB access, no side effects.
 *
 * Exports:
 * - ``truncate``               — Truncates a string to a max length with "..."
 * - ``computeHasWaitingInput`` — Derives hasWaitingInput flag for a WIP task
 * - ``deriveWaitingItems``     — Pure derivation of RadarWaitingItem[] from props
 * - ``useWaitingInputZone``    — React hook returning waitingItems and respondToItem
 *
 * Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5,
 *               8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
 */

import { useMemo, useCallback } from 'react';
import type { RadarWaitingItem, RadarWipTask } from '../../../../../types';
import type { PendingQuestion, PermissionRequest } from '../../../types';
import { sortWaitingItems } from '../radarSortUtils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseWaitingInputZoneParams {
  pendingQuestion: PendingQuestion | null;
  pendingPermission: PermissionRequest | null;
  activeSessionId: string | undefined;
  wipTasks: RadarWipTask[];
}

export interface UseWaitingInputZoneReturn {
  waitingItems: RadarWaitingItem[];
  respondToItem: (itemId: string) => void;
}

// ---------------------------------------------------------------------------
// truncate
// ---------------------------------------------------------------------------

/**
 * Truncates a string to maxLength characters.
 * If the string exceeds maxLength, appends "..." so the total is maxLength.
 */
export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength - 3) + '...';
}

// ---------------------------------------------------------------------------
// computeHasWaitingInput
// ---------------------------------------------------------------------------

/**
 * Computes hasWaitingInput for a WIP task.
 * Returns true iff the task's sessionId matches activeSessionId
 * AND at least one of pendingQuestion/pendingPermission is non-null.
 */
export function computeHasWaitingInput(
  task: RadarWipTask,
  activeSessionId: string | undefined,
  pendingQuestion: PendingQuestion | null,
  pendingPermission: PermissionRequest | null,
): boolean {
  if (!activeSessionId) return false;
  if (task.sessionId !== activeSessionId) return false;
  return pendingQuestion !== null || pendingPermission !== null;
}

// ---------------------------------------------------------------------------
// deriveWaitingItems
// ---------------------------------------------------------------------------

/**
 * Pure derivation function — no side effects, no API calls, no state mutations.
 * Exported for direct unit and property-based testing.
 *
 * Derives RadarWaitingItem[] from pendingQuestion/pendingPermission SSE props
 * by matching activeSessionId against WIP tasks to look up task metadata.
 */
export function deriveWaitingItems(
  pendingQuestion: PendingQuestion | null,
  pendingPermission: PermissionRequest | null,
  activeSessionId: string | undefined,
  wipTasks: RadarWipTask[],
): RadarWaitingItem[] {
  const items: RadarWaitingItem[] = [];

  // Find the WIP task matching the active session
  const matchedTask = wipTasks.find(
    (t) => t.sessionId === activeSessionId,
  );

  if (pendingQuestion !== null) {
    items.push({
      id: pendingQuestion.toolUseId,
      title: matchedTask?.title ?? 'Agent Question',
      agentId: matchedTask?.agentId ?? '',
      sessionId: activeSessionId ?? null,
      question: truncate(
        pendingQuestion.questions[0]?.question ?? 'Pending question',
        200,
      ),
      createdAt: matchedTask?.startedAt ?? new Date().toISOString(),
    });
  }

  if (pendingPermission !== null) {
    items.push({
      id: pendingPermission.requestId,
      title: matchedTask?.title ?? 'Permission Required',
      agentId: matchedTask?.agentId ?? '',
      sessionId: activeSessionId ?? null,
      question: truncate(pendingPermission.reason, 200),
      createdAt: matchedTask?.startedAt ?? new Date().toISOString(),
    });
  }

  return sortWaitingItems(items);
}

// ---------------------------------------------------------------------------
// useWaitingInputZone hook
// ---------------------------------------------------------------------------

/**
 * React hook for the Waiting Input zone within Needs Attention.
 *
 * Uses useMemo to derive RadarWaitingItem[] from SSE props, recomputing
 * only when inputs change. Provides respondToItem action handler.
 */
export function useWaitingInputZone({
  pendingQuestion,
  pendingPermission,
  activeSessionId,
  wipTasks,
}: UseWaitingInputZoneParams): UseWaitingInputZoneReturn {
  const waitingItems = useMemo(
    () =>
      deriveWaitingItems(
        pendingQuestion,
        pendingPermission,
        activeSessionId,
        wipTasks,
      ),
    [pendingQuestion, pendingPermission, activeSessionId, wipTasks],
  );

  const respondToItem = useCallback(
    (itemId: string) => {
      const item = waitingItems.find((w) => w.id === itemId);
      if (!item?.sessionId) return;

      // TODO: Integrate with useTabState hook for tab navigation.
      // This will be wired in task 6.2 — for now, log the intent.
      // eslint-disable-next-line no-console
      console.log(
        `[useWaitingInputZone] respondToItem: navigate to session ${item.sessionId}`,
      );
    },
    [waitingItems],
  );

  return { waitingItems, respondToItem };
}
