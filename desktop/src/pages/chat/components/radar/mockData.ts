/**
 * Mock data factory functions for all Swarm Radar zones.
 *
 * Each factory returns a new array on every call (no shared mutable state).
 * All IDs follow the `mock-{zone}-{index}` pattern for stability.
 *
 * - ``getMockTodos``          — ≥3 ToDo items with varied priorities/sources
 * - ``getMockWaitingItems``   — ≥2 waiting input items
 * - ``getMockWipTasks``       — ≥2 WIP tasks (wip + draft)
 * - ``getMockCompletedTasks`` — ≥3 completed tasks within 7-day window
 * - ``getMockSystemJobs``     — ≥2 system autonomous jobs
 * - ``getMockUserJobs``       — ≥2 user-defined autonomous jobs
 */

import type {
  RadarTodo,
  RadarWaitingItem,
  RadarWipTask,
  RadarCompletedTask,
  RadarAutonomousJob,
} from '../../../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

function hoursAgo(n: number): string {
  const d = new Date();
  d.setHours(d.getHours() - n);
  return d.toISOString();
}

function today(): string {
  return new Date().toISOString().slice(0, 10) + 'T00:00:00.000Z';
}

// ---------------------------------------------------------------------------
// ToDos
// ---------------------------------------------------------------------------

export function getMockTodos(): RadarTodo[] {
  const now = new Date().toISOString();
  return [
    {
      id: 'mock-todo-1',
      workspaceId: 'swarmws',
      title: 'Review PR #123 — auth refactor',
      description: 'Security-critical changes need review before merge',
      source: 'GitHub notification',
      sourceType: 'integration',
      status: 'overdue',
      priority: 'high',
      dueDate: daysAgo(1),
      linkedContext: null,
      taskId: null,
      createdAt: daysAgo(3),
      updatedAt: now,
    },
    {
      id: 'mock-todo-2',
      workspaceId: 'swarmws',
      title: 'Prepare Q2 planning doc',
      description: 'Draft the quarterly planning document',
      source: null,
      sourceType: 'manual',
      status: 'pending',
      priority: 'medium',
      dueDate: today(),
      linkedContext: null,
      taskId: null,
      createdAt: daysAgo(2),
      updatedAt: now,
    },
    {
      id: 'mock-todo-3',
      workspaceId: 'swarmws',
      title: 'Update onboarding docs',
      description: null,
      source: 'Team Slack',
      sourceType: 'slack',
      status: 'pending',
      priority: 'low',
      dueDate: null,
      linkedContext: null,
      taskId: null,
      createdAt: daysAgo(1),
      updatedAt: now,
    },
  ];
}

// ---------------------------------------------------------------------------
// Waiting Input Items
// ---------------------------------------------------------------------------

export function getMockWaitingItems(): RadarWaitingItem[] {
  return [
    {
      id: 'mock-waiting-1',
      title: 'Draft client email',
      agentId: 'agent-default',
      sessionId: 'session-mock-1',
      question: 'Should the tone be formal or friendly for the client response?',
      createdAt: hoursAgo(1),
    },
    {
      id: 'mock-waiting-2',
      title: 'Deploy staging build',
      agentId: 'agent-default',
      sessionId: 'session-mock-2',
      question: 'Permission requested: execute bash command `npm run deploy:staging`',
      createdAt: hoursAgo(2),
    },
  ];
}

// ---------------------------------------------------------------------------
// WIP Tasks
// ---------------------------------------------------------------------------

export function getMockWipTasks(): RadarWipTask[] {
  return [
    {
      id: 'mock-wip-1',
      workspaceId: 'swarmws',
      agentId: 'agent-default',
      sessionId: 'session-mock-3',
      status: 'wip',
      title: 'Implement user authentication flow',
      description: 'Setting up OAuth2 with PKCE flow',
      priority: 'high',
      sourceTodoId: null,
      model: 'claude-sonnet-4-20250514',
      createdAt: hoursAgo(3),
      startedAt: hoursAgo(2),
      error: null,
      hasWaitingInput: false,
    },
    {
      id: 'mock-wip-2',
      workspaceId: 'swarmws',
      agentId: 'agent-default',
      sessionId: 'session-mock-4',
      status: 'draft',
      title: 'Generate API documentation',
      description: 'Queued — waiting for auth flow to complete',
      priority: 'medium',
      sourceTodoId: null,
      model: 'claude-sonnet-4-20250514',
      createdAt: hoursAgo(1),
      startedAt: null,
      error: null,
      hasWaitingInput: false,
    },
  ];
}

// ---------------------------------------------------------------------------
// Completed Tasks
// ---------------------------------------------------------------------------

export function getMockCompletedTasks(): RadarCompletedTask[] {
  return [
    {
      id: 'mock-completed-1',
      workspaceId: 'swarmws',
      agentId: 'agent-default',
      sessionId: 'session-mock-5',
      title: 'Set up CI/CD pipeline',
      description: 'Configured GitHub Actions with staging and prod',
      priority: 'high',
      completedAt: hoursAgo(2),
      reviewRequired: false,
      reviewRiskLevel: null,
    },
    {
      id: 'mock-completed-2',
      workspaceId: 'swarmws',
      agentId: 'agent-default',
      sessionId: 'session-mock-6',
      title: 'Database schema migration',
      description: 'Added workspace_id column to tasks table',
      priority: 'medium',
      completedAt: daysAgo(1),
      reviewRequired: false,
      reviewRiskLevel: null,
    },
    {
      id: 'mock-completed-3',
      workspaceId: 'swarmws',
      agentId: 'agent-default',
      sessionId: 'session-mock-7',
      title: 'Fix login redirect bug',
      description: 'Resolved OAuth callback URL mismatch',
      priority: 'high',
      completedAt: daysAgo(3),
      reviewRequired: false,
      reviewRiskLevel: null,
    },
  ];
}

// ---------------------------------------------------------------------------
// Autonomous Jobs — System
// ---------------------------------------------------------------------------

export function getMockSystemJobs(): RadarAutonomousJob[] {
  return [
    {
      id: 'mock-job-sys-1',
      name: 'Workspace Sync',
      category: 'system',
      status: 'running',
      schedule: null,
      lastRunAt: hoursAgo(0.1),
      nextRunAt: null,
      description: 'Syncs workspace files with the knowledge index',
    },
    {
      id: 'mock-job-sys-2',
      name: 'Knowledge Indexing',
      category: 'system',
      status: 'running',
      schedule: null,
      lastRunAt: hoursAgo(0.5),
      nextRunAt: null,
      description: 'Indexes workspace content for semantic search',
    },
  ];
}

// ---------------------------------------------------------------------------
// Autonomous Jobs — User-Defined
// ---------------------------------------------------------------------------

export function getMockUserJobs(): RadarAutonomousJob[] {
  return [
    {
      id: 'mock-job-user-1',
      name: 'Daily Digest',
      category: 'user_defined',
      status: 'running',
      schedule: 'Daily at 9am',
      lastRunAt: daysAgo(0),
      nextRunAt: null,
      description: 'Summarizes yesterday activity and today priorities',
    },
    {
      id: 'mock-job-user-2',
      name: 'Weekly Report',
      category: 'user_defined',
      status: 'paused',
      schedule: 'Every Monday',
      lastRunAt: daysAgo(7),
      nextRunAt: null,
      description: 'Generates weekly progress report',
    },
  ];
}
