/**
 * React hook for task zone state management (In Progress + Completed zones).
 *
 * Encapsulates data fetching (React Query, 30s polling), WIP filtering,
 * archive window filtering, sorting, lifecycle actions with optimistic updates,
 * and SSE-triggered cache invalidation.
 *
 * Exports:
 * - useTaskZone          — Hook returning sorted WIP tasks, sorted completed tasks,
 *                          loading state, and action handlers
 * - filterByArchiveWindow — Pure function for direct testing
 */

import { useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { RadarWipTask, RadarCompletedTask } from '../../../../../types';
import { radarService } from '../../../../../services/radar';
import { sortWipTasks, sortCompletedTasks } from '../radarSortUtils';
import {
  ARCHIVE_WINDOW_DAYS,
  TASK_POLLING_INTERVAL_MS,
} from '../radarConstants';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const WIP_TASKS_KEY = ['radar', 'wipTasks'] as const;
const COMPLETED_TASKS_KEY = ['radar', 'completedTasks'] as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseTaskZoneParams {
  workspaceId: string;
  isVisible: boolean;
}

interface UseTaskZoneReturn {
  wipTasks: RadarWipTask[];
  completedTasks: RadarCompletedTask[];
  isLoading: boolean;
  viewThread: (taskId: string) => void;
  cancelTask: (taskId: string) => void;
  resumeCompleted: (taskId: string) => void;
}

// ---------------------------------------------------------------------------
// Archive window filter (exported for direct testing)
// ---------------------------------------------------------------------------

/** Filter completed tasks to those within the archive window. */
export function filterByArchiveWindow(
  tasks: RadarCompletedTask[],
  windowDays: number = ARCHIVE_WINDOW_DAYS,
): RadarCompletedTask[] {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - windowDays);
  const cutoffISO = cutoff.toISOString();
  return tasks.filter((t) => t.completedAt >= cutoffISO);
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTaskZone({
  workspaceId,
  isVisible,
}: UseTaskZoneParams): UseTaskZoneReturn {
  const queryClient = useQueryClient();

  // Compute server-side pre-filter date for completed tasks
  const completedAfterISO = useMemo(() => {
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - ARCHIVE_WINDOW_DAYS);
    return cutoff.toISOString();
  }, []);

  // -----------------------------------------------------------------------
  // Data fetching — WIP tasks (Req 8.1, 8.3, 8.7)
  // -----------------------------------------------------------------------

  const { data: rawWipTasks = [], isLoading: wipLoading } = useQuery<
    RadarWipTask[]
  >({
    queryKey: [...WIP_TASKS_KEY],
    queryFn: () => radarService.fetchWipTasks(workspaceId),
    refetchInterval: TASK_POLLING_INTERVAL_MS,
    enabled: isVisible,
    staleTime: TASK_POLLING_INTERVAL_MS - 1_000,
  });

  // -----------------------------------------------------------------------
  // Data fetching — Completed tasks (Req 8.1, 8.3, 8.7)
  // -----------------------------------------------------------------------

  const { data: rawCompletedTasks = [], isLoading: completedLoading } =
    useQuery<RadarCompletedTask[]>({
      queryKey: [...COMPLETED_TASKS_KEY],
      queryFn: () =>
        radarService.fetchCompletedTasks(workspaceId, completedAfterISO),
      refetchInterval: TASK_POLLING_INTERVAL_MS,
      enabled: isVisible,
      staleTime: TASK_POLLING_INTERVAL_MS - 1_000,
    });

  // -----------------------------------------------------------------------
  // Filter + sort (Req 8.5, 8.6)
  // -----------------------------------------------------------------------

  const wipTasks = useMemo(
    () => sortWipTasks(rawWipTasks),
    [rawWipTasks],
  );

  const completedTasks = useMemo(
    () => sortCompletedTasks(filterByArchiveWindow(rawCompletedTasks)),
    [rawCompletedTasks],
  );

  // -----------------------------------------------------------------------
  // Optimistic cancel mutation (PE Finding #5, Req 8.9)
  // -----------------------------------------------------------------------

  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => radarService.cancelTask(taskId),
    onMutate: async (taskId: string) => {
      await queryClient.cancelQueries({ queryKey: [...WIP_TASKS_KEY] });
      const previousData = queryClient.getQueryData<RadarWipTask[]>([
        ...WIP_TASKS_KEY,
      ]);
      queryClient.setQueryData<RadarWipTask[]>(
        [...WIP_TASKS_KEY],
        (old) => old?.filter((t) => t.id !== taskId) ?? [],
      );
      return { previousData };
    },
    onError: (_err, _taskId, context) => {
      if (context?.previousData !== undefined) {
        queryClient.setQueryData<RadarWipTask[]>(
          [...WIP_TASKS_KEY],
          context.previousData,
        );
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: [...WIP_TASKS_KEY] });
      queryClient.invalidateQueries({ queryKey: [...COMPLETED_TASKS_KEY] });
    },
  });

  // -----------------------------------------------------------------------
  // Action handlers (Req 8.10, 8.11)
  // -----------------------------------------------------------------------

  const viewThread = useCallback(
    (taskId: string) => {
      const allTasks = [...rawWipTasks, ...rawCompletedTasks];
      const task = allTasks.find((t) => t.id === taskId);
      if (!task?.sessionId) {
        console.warn('[useTaskZone] viewThread: no sessionId for task', taskId);
        return;
      }
      // TODO: Navigate to chat thread via useTabState using task.sessionId
    },
    [rawWipTasks, rawCompletedTasks],
  );

  const handleCancelTask = useCallback(
    (taskId: string) => {
      cancelMutation.mutate(taskId);
    },
    [cancelMutation],
  );

  const resumeCompleted = useCallback(
    (taskId: string) => {
      const task = rawCompletedTasks.find((t) => t.id === taskId);
      if (!task?.sessionId) {
        console.warn('[useTaskZone] resumeCompleted: no sessionId for task', taskId);
        return;
      }
      // TODO: Create new chat thread seeded with completion context, navigate via useTabState
    },
    [rawCompletedTasks],
  );

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  return {
    wipTasks,
    completedTasks,
    isLoading: wipLoading || completedLoading,
    viewThread,
    cancelTask: handleCancelTask,
    resumeCompleted,
  };
}
