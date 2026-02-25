/**
 * Hook for polling running task count (for sidebar badge).
 */
import { useQuery } from '@tanstack/react-query';
import { tasksService } from '../services/tasks';

const POLL_INTERVAL = 5000; // 5 seconds

export function useRunningTaskCount() {
  const { data: count = 0, isLoading, error } = useQuery({
    queryKey: ['runningTaskCount'],
    queryFn: () => tasksService.getRunningCount(),
    refetchInterval: POLL_INTERVAL,
    staleTime: POLL_INTERVAL - 1000, // Slightly less than poll interval
  });

  return { count, isLoading, error };
}
