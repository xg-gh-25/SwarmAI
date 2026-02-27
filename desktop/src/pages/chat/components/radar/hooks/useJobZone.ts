/**
 * React hook for autonomous job zone state management.
 *
 * Encapsulates data fetching via React Query with 60-second polling,
 * gated by sidebar visibility. Partitions jobs into system and user-defined
 * categories, applies sorting, and exposes error-state jobs for cross-zone
 * referencing in the Needs Attention zone.
 *
 * Exports:
 * - useJobZone — Hook returning sorted system/user jobs, error jobs,
 *                loading state, and click handler
 */

import { useMemo, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { RadarAutonomousJob } from '../../../../../types';
import { radarService } from '../../../../../services/radar';
import { sortAutonomousJobs } from '../radarSortUtils';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const JOB_POLLING_INTERVAL_MS = 60_000;
const JOBS_QUERY_KEY = ['radar', 'autonomousJobs'] as const;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface UseJobZoneParams {
  isVisible: boolean;
}

interface UseJobZoneReturn {
  systemJobs: RadarAutonomousJob[];
  userJobs: RadarAutonomousJob[];
  errorJobs: RadarAutonomousJob[];
  isLoading: boolean;
  handleJobClick: (jobId: string) => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useJobZone({
  isVisible,
}: UseJobZoneParams): UseJobZoneReturn {
  // Data fetching — 60s polling, gated by visibility (Req 7.3, 7.7)
  const { data, isLoading } = useQuery<RadarAutonomousJob[]>({
    queryKey: [...JOBS_QUERY_KEY],
    queryFn: () => radarService.fetchAutonomousJobs(),
    refetchInterval: JOB_POLLING_INTERVAL_MS,
    enabled: isVisible,
    staleTime: JOB_POLLING_INTERVAL_MS - 1_000,
  });

  const allJobs = data ?? [];

  // Sort all jobs using Spec 1's sortAutonomousJobs
  const sorted = useMemo(() => sortAutonomousJobs(allJobs), [allJobs]);

  // Partition by category (Req 7.4)
  const systemJobs = useMemo(
    () => sorted.filter((j) => j.category === 'system'),
    [sorted],
  );
  const userJobs = useMemo(
    () => sorted.filter((j) => j.category === 'user_defined'),
    [sorted],
  );

  // Extract error-state jobs for cross-zone reference (Req 3.1)
  const errorJobs = useMemo(
    () => allJobs.filter((j) => j.status === 'error'),
    [allJobs],
  );

  // No-op click handler — tooltip managed locally in AutonomousJobItem
  const handleJobClick = useCallback((_jobId: string) => {
    // Future: open job configuration panel
  }, []);

  return { systemJobs, userJobs, errorJobs, isLoading, handleJobClick };
}
