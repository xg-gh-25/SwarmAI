/**
 * React hook for ToDo zone state management within the Needs Attention zone.
 *
 * Encapsulates data fetching (React Query, 30s polling), active filtering,
 * sorting, lifecycle actions with optimistic updates, and quick-add.
 *
 * Exports:
 * - useTodoZone — Hook returning sorted active todos, loading state, and action handlers
 *
 * Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9,
 *               2.2, 2.4, 2.5, 2.6, 2.9, 2.10, 4.1, 4.2
 */

import { useMemo, useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { RadarTodo } from '../../../../../types';
import { radarService } from '../../../../../services/radar';
import { agentsService } from '../../../../../services/agents';
import { sortTodos } from '../radarSortUtils';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RADAR_TODOS_KEY = ['radar', 'todos'] as const;
const POLL_INTERVAL = 30_000; // 30 seconds (Req 7.2)

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseTodoZoneParams {
  workspaceId: string;
  isVisible: boolean; // From rightSidebars.isActive('todoRadar')
}

interface UseTodoZoneReturn {
  todos: RadarTodo[];
  isLoading: boolean;
  startError: string | null;
  quickAddTodo: (title: string) => Promise<void>;
  startTodo: (todoId: string) => void;
  editTodo: (todoId: string) => string;
  completeTodo: (todoId: string) => void;
  cancelTodo: (todoId: string) => void;
  deleteTodo: (todoId: string) => void;
}

// ---------------------------------------------------------------------------
// Active filter helper
// ---------------------------------------------------------------------------

/** Returns only pending and overdue todos (Req 7.4). */
function filterActive(todos: RadarTodo[]): RadarTodo[] {
  return todos.filter(
    (t) => t.status === 'pending' || t.status === 'overdue',
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useTodoZone({
  workspaceId,
  isVisible,
}: UseTodoZoneParams): UseTodoZoneReturn {
  const queryClient = useQueryClient();
  const [startError, setStartError] = useState<string | null>(null);

  // -------------------------------------------------------------------------
  // Data fetching — React Query with 30s polling (Req 7.2, 7.8, 7.9)
  // -------------------------------------------------------------------------

  const { data: rawTodos = [], isLoading } = useQuery<RadarTodo[]>({
    queryKey: [...RADAR_TODOS_KEY],
    queryFn: () => radarService.fetchActiveTodos(workspaceId),
    refetchInterval: POLL_INTERVAL,
    enabled: isVisible, // Zero queries when sidebar hidden (Req 7.9)
    staleTime: POLL_INTERVAL - 1_000,
  });

  // -------------------------------------------------------------------------
  // Filter active + sort (Req 7.4, 7.5)
  // -------------------------------------------------------------------------

  const todos = useMemo(
    () => sortTodos(filterActive(rawTodos)),
    [rawTodos],
  );

  // -------------------------------------------------------------------------
  // Optimistic update helpers (Req 2.9, 7.6)
  // -------------------------------------------------------------------------

  type TodoSnapshot = RadarTodo[] | undefined;

  /** Snapshot current cache and remove a todo optimistically. */
  const optimisticRemove = (todoId: string): { snapshot: TodoSnapshot } => {
    const snapshot = queryClient.getQueryData<RadarTodo[]>([...RADAR_TODOS_KEY]);
    queryClient.setQueryData<RadarTodo[]>(
      [...RADAR_TODOS_KEY],
      (old) => old?.filter((t) => t.id !== todoId) ?? [],
    );
    return { snapshot };
  };

  const restoreSnapshot = (snapshot: TodoSnapshot) => {
    if (snapshot !== undefined) {
      queryClient.setQueryData<RadarTodo[]>([...RADAR_TODOS_KEY], snapshot);
    }
  };

  const invalidateTodos = () => {
    queryClient.invalidateQueries({ queryKey: [...RADAR_TODOS_KEY] });
  };

  // -------------------------------------------------------------------------
  // completeTodo — status → 'handled' (Req 2.4)
  // -------------------------------------------------------------------------

  const completeMutation = useMutation({
    mutationFn: (todoId: string) =>
      radarService.updateTodoStatus(todoId, 'handled'),
    onMutate: (todoId) => optimisticRemove(todoId),
    onError: (_err, _todoId, context) => {
      restoreSnapshot(context?.snapshot);
    },
    onSettled: invalidateTodos,
  });

  // -------------------------------------------------------------------------
  // cancelTodo — status → 'cancelled' (Req 2.5)
  // -------------------------------------------------------------------------

  const cancelMutation = useMutation({
    mutationFn: (todoId: string) =>
      radarService.updateTodoStatus(todoId, 'cancelled'),
    onMutate: (todoId) => optimisticRemove(todoId),
    onError: (_err, _todoId, context) => {
      restoreSnapshot(context?.snapshot);
    },
    onSettled: invalidateTodos,
  });

  // -------------------------------------------------------------------------
  // deleteTodo — status → 'deleted' (Req 2.6)
  // -------------------------------------------------------------------------

  const deleteMutation = useMutation({
    mutationFn: (todoId: string) =>
      radarService.updateTodoStatus(todoId, 'deleted'),
    onMutate: (todoId) => optimisticRemove(todoId),
    onError: (_err, _todoId, context) => {
      restoreSnapshot(context?.snapshot);
    },
    onSettled: invalidateTodos,
  });

  // -------------------------------------------------------------------------
  // quickAddTodo (Req 3.3, 3.5)
  // -------------------------------------------------------------------------

  const quickAddMutation = useMutation({
    mutationFn: (title: string) =>
      radarService.createTodo({
        workspaceId,
        title,
        sourceType: 'manual',
        priority: 'none',
      }),
    onMutate: (title) => {
      const snapshot = queryClient.getQueryData<RadarTodo[]>([
        ...RADAR_TODOS_KEY,
      ]);
      // Optimistic: append a placeholder todo
      const now = new Date().toISOString();
      const placeholder: RadarTodo = {
        id: `temp-${Date.now()}`,
        workspaceId,
        title,
        description: null,
        source: null,
        sourceType: 'manual',
        status: 'pending',
        priority: 'none',
        dueDate: null,
        linkedContext: null,
        taskId: null,
        createdAt: now,
        updatedAt: now,
      };
      queryClient.setQueryData<RadarTodo[]>(
        [...RADAR_TODOS_KEY],
        (old) => [...(old ?? []), placeholder],
      );
      return { snapshot };
    },
    onError: (_err, _title, context) => {
      restoreSnapshot(context?.snapshot);
    },
    onSettled: invalidateTodos,
  });

  // -------------------------------------------------------------------------
  // startTodo — resolve default agent, convert to task (Req 2.2, 7.7, 4.1)
  // -------------------------------------------------------------------------

  const startMutation = useMutation({
    mutationFn: async (todoId: string) => {
      // Resolve default agent (PE Finding #4)
      let agent;
      try {
        agent = await agentsService.getDefault();
      } catch {
        throw new Error('No default agent configured.');
      }
      if (!agent?.id) {
        throw new Error('No default agent configured.');
      }
      const result = await radarService.convertTodoToTask(todoId, agent.id);
      return result;
    },
    onMutate: (todoId) => {
      setStartError(null);
      return optimisticRemove(todoId);
    },
    onError: (err, _todoId, context) => {
      restoreSnapshot(context?.snapshot);
      const message =
        err instanceof Error ? err.message : 'Failed to start ToDo.';
      setStartError(message);
    },
    onSettled: invalidateTodos,
    onSuccess: (_data) => {
      // TODO: Navigate to chat thread via useTabState once wiring
      // is complete in task 8.1. The convertTodoToTask response
      // contains the new task/session info for tab navigation.
    },
  });

  // -------------------------------------------------------------------------
  // editTodo — signals inline edit mode in TodoItem (no API call)
  // -------------------------------------------------------------------------

  const editTodo = useCallback((todoId: string): string => {
    return todoId;
  }, []);

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------

  return {
    todos,
    isLoading,
    startError,
    quickAddTodo: (title: string) => quickAddMutation.mutateAsync(title).then(() => {}),
    startTodo: (todoId: string) => startMutation.mutate(todoId),
    editTodo,
    completeTodo: (todoId: string) => completeMutation.mutate(todoId),
    cancelTodo: (todoId: string) => cancelMutation.mutate(todoId),
    deleteTodo: (todoId: string) => deleteMutation.mutate(todoId),
  };
}
