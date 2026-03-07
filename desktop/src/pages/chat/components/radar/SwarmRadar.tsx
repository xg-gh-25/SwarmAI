/**
 * Swarm Radar — unified attention & action control panel.
 *
 * Root shell component replacing TodoRadarSidebar. Renders four
 * collapsible RadarZone components. The Needs Attention zone uses
 * real ToDo data via ``useTodoZone`` hook with ``QuickAddTodo`` and
 * ``TodoList`` components. The Autonomous Jobs zone uses real data
 * via ``useJobZone`` hook with ``AutonomousJobList`` component.
 * Error-state jobs cross-reference into the Needs Attention zone.
 *
 * Props ``pendingQuestion`` and ``pendingPermission`` are passed through
 * for Spec 3 (Waiting Input) integration.
 *
 * - ``SwarmRadar``      — Shell component
 * - ``SwarmRadarProps``  — Props interface
 */

import { useState, useRef } from 'react';
import clsx from 'clsx';
import type { RadarZoneId } from '../../../../types';
import type { PendingQuestion, PermissionRequest } from '../../types';
import { RadarZone } from './RadarZone';
import { getBadgeTint } from './radarIndicators';
import { sortWipTasks } from './radarSortUtils';
import { useTodoZone } from './hooks/useTodoZone';
import { useWaitingInputZone, computeHasWaitingInput } from './hooks/useWaitingInputZone';
import { useTaskZone } from './hooks/useTaskZone';
import { useJobZone } from './hooks/useJobZone';
import { QuickAddTodo } from './QuickAddTodo';
import { TodoList } from './TodoList';
import { WaitingInputList } from './WaitingInputList';
import { WipTaskList } from './WipTaskList';
import { CompletedTaskList } from './CompletedTaskList';
import { AutonomousJobList } from './AutonomousJobList';
import { EvolutionBadge } from './EvolutionBadge';
import type { EvolutionSessionCount } from './EvolutionBadge';
import './SwarmRadar.css';

export interface SwarmRadarProps {
  width: number;
  isResizing: boolean;
  onClose?: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
  pendingQuestion?: PendingQuestion | null;
  pendingPermission?: PermissionRequest | null;
  activeSessionId?: string;
  /** Successful evolution counts for the current session (Phase 2). */
  evolutionCounts?: EvolutionSessionCount;
}

// Zone expand/collapse default state
const DEFAULT_EXPANDED: Record<RadarZoneId, boolean> = {
  needsAttention: true,
  inProgress: true,
  completed: true,
  autonomousJobs: true,
};

// Empty state messages per zone
const EMPTY_MESSAGES: Record<RadarZoneId, string> = {
  needsAttention: 'All clear — nothing needs your attention right now.',
  inProgress: 'No tasks running. Start a ToDo or chat to kick things off.',
  completed: 'No completed tasks in the last 7 days.',
  autonomousJobs: 'No autonomous jobs configured yet.',
};

export function SwarmRadar({
  width,
  isResizing,
  onClose,
  onMouseDown,
  pendingQuestion,
  pendingPermission,
  activeSessionId,
  evolutionCounts,
}: SwarmRadarProps) {
  const [expanded, setExpanded] = useState(DEFAULT_EXPANDED);

  const toggle = (zoneId: RadarZoneId) =>
    setExpanded((prev) => ({ ...prev, [zoneId]: !prev[zoneId] }));

  // Ref for scrolling to Autonomous Jobs zone from cross-zone error links
  const jobsZoneRef = useRef<HTMLDivElement>(null);
  const scrollToJobsZone = () =>
    jobsZoneRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Real ToDo data via useTodoZone hook (Req 7.1)
  const {
    todos,
    quickAddTodo,
    startTodo,
    editTodo,
    completeTodo,
    cancelTodo,
    deleteTodo,
  } = useTodoZone({ workspaceId: 'swarmws', isVisible: true });

  // Real task data via useTaskZone hook (Spec 4)
  const {
    wipTasks,
    completedTasks,
    viewThread,
    cancelTask,
    resumeCompleted,
  } = useTaskZone({ workspaceId: 'swarmws', isVisible: true });

  // Waiting Input data via useWaitingInputZone hook (Spec 3)
  const { waitingItems, respondToItem } = useWaitingInputZone({
    pendingQuestion: pendingQuestion ?? null,
    pendingPermission: pendingPermission ?? null,
    activeSessionId,
    wipTasks,
  });

  // Augment WIP tasks with hasWaitingInput for display
  const augmentedWipTasks = sortWipTasks(
    wipTasks.map((t) => ({
      ...t,
      hasWaitingInput: computeHasWaitingInput(
        t,
        activeSessionId,
        pendingQuestion ?? null,
        pendingPermission ?? null,
      ),
    })),
  );

  // Real autonomous job data via useJobZone hook (Spec 5)
  const {
    systemJobs,
    userJobs,
    errorJobs,
    handleJobClick,
  } = useJobZone({ isVisible: true });

  // Badge counts
  const needsAttentionCount = todos.length + waitingItems.length + errorJobs.length;
  const inProgressCount = augmentedWipTasks.length;
  const completedCount = completedTasks.length;
  const jobsCount = systemJobs.length + userJobs.length;

  return (
    <div
      className="swarm-radar"
      style={{ width }}
      role="region"
      aria-label="Swarm Radar"
    >
      {/* Resize handle (left edge) */}
      <div
        className={clsx('swarm-radar-resize', isResizing && 'swarm-radar-resize--active')}
        onMouseDown={onMouseDown}
      >
        <div className="swarm-radar-resize-hitarea" />
      </div>

      {/* Header */}
      <div className="swarm-radar-header">
        <div className="swarm-radar-header-title">
          <span className="material-symbols-outlined">radar</span>
          <span>Swarm Radar</span>
          {evolutionCounts && <EvolutionBadge counts={evolutionCounts} />}
        </div>
        {onClose && (
          <button
            className="swarm-radar-close"
            onClick={onClose}
            aria-label="Close Swarm Radar"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
          </button>
        )}
      </div>

      {/* Scrollable content */}
      <div className="swarm-radar-content" aria-live="polite">
        {/* Needs Attention */}
        <RadarZone
          zoneId="needsAttention"
          emoji="🔴"
          label="Needs Attention"
          count={needsAttentionCount}
          badgeTint={getBadgeTint('needsAttention', { todos })}
          isExpanded={expanded.needsAttention}
          onToggle={() => toggle('needsAttention')}
          emptyMessage={EMPTY_MESSAGES.needsAttention}
        >
          <QuickAddTodo onAdd={quickAddTodo} />
          <TodoList
            todos={todos}
            onStart={startTodo}
            onEdit={editTodo}
            onComplete={completeTodo}
            onCancel={cancelTodo}
            onDelete={deleteTodo}
          />
          <WaitingInputList waitingItems={waitingItems} onRespond={respondToItem} />
          {errorJobs.length > 0 && (
            <div className="radar-error-jobs" role="list" aria-label="Jobs with errors">
              {errorJobs.map((job) => (
                <div key={job.id} role="listitem" className="radar-error-job-item">
                  <span className="radar-error-job-name">{job.name}</span>
                  <span className="radar-error-job-status">❌ Error</span>
                  <button
                    className="radar-error-job-link"
                    onClick={() => scrollToJobsZone()}
                    aria-label={`View ${job.name} in Jobs zone`}
                  >
                    View in Jobs
                  </button>
                </div>
              ))}
            </div>
          )}
        </RadarZone>

        {/* In Progress */}
        <RadarZone
          zoneId="inProgress"
          emoji="🟡"
          label="In Progress"
          count={inProgressCount}
          badgeTint={getBadgeTint('inProgress')}
          isExpanded={expanded.inProgress}
          onToggle={() => toggle('inProgress')}
          emptyMessage={EMPTY_MESSAGES.inProgress}
        >
          <WipTaskList
            tasks={augmentedWipTasks}
            onViewThread={viewThread}
            onCancel={cancelTask}
          />
        </RadarZone>

        {/* Completed */}
        <RadarZone
          zoneId="completed"
          emoji="🟢"
          label="Completed"
          count={completedCount}
          badgeTint={getBadgeTint('completed')}
          isExpanded={expanded.completed}
          onToggle={() => toggle('completed')}
          emptyMessage={EMPTY_MESSAGES.completed}
        >
          <CompletedTaskList
            tasks={completedTasks}
            onViewThread={viewThread}
            onResume={resumeCompleted}
          />
        </RadarZone>

        {/* Autonomous Jobs */}
        <div ref={jobsZoneRef}>
        <RadarZone
          zoneId="autonomousJobs"
          emoji="🤖"
          label="Autonomous Jobs"
          count={jobsCount}
          badgeTint={getBadgeTint('autonomousJobs', { jobs: [...systemJobs, ...userJobs] })}
          isExpanded={expanded.autonomousJobs}
          onToggle={() => toggle('autonomousJobs')}
          emptyMessage={EMPTY_MESSAGES.autonomousJobs}
        >
          <AutonomousJobList
            systemJobs={systemJobs}
            userJobs={userJobs}
            onJobClick={handleJobClick}
          />
        </RadarZone>
        </div>
      </div>
    </div>
  );
}
