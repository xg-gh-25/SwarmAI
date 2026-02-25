import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { tasksService } from '../services/tasks';
import { agentsService } from '../services/agents';
import { ConfirmDialog } from '../components/common';
import type { Task, TaskStatus, Agent } from '../types';

// Format relative time
function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
}

// Format duration
function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '-';
  const start = new Date(startedAt);
  const end = completedAt ? new Date(completedAt) : new Date();
  const diffMs = end.getTime() - start.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const mins = Math.floor(diffSecs / 60);
  const secs = diffSecs % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Status badge component
function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation();

  const config = {
    pending: { color: 'bg-gray-500/20 text-gray-400', icon: 'schedule' },
    running: { color: 'bg-blue-500/20 text-blue-400', icon: 'sync', spin: true },
    completed: { color: 'bg-green-500/20 text-green-400', icon: 'check_circle' },
    failed: { color: 'bg-red-500/20 text-red-400', icon: 'error' },
    cancelled: { color: 'bg-gray-500/20 text-gray-400', icon: 'cancel' },
  };

  const { color, icon, spin } = config[status] || config.pending;

  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', color)}>
      <span className={clsx('material-symbols-outlined text-sm', spin && 'animate-spin')}>{icon}</span>
      {t(`tasks.status.${status}`)}
    </span>
  );
}

export default function TasksPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'all'>('all');
  const [taskToCancel, setTaskToCancel] = useState<Task | null>(null);
  const [taskToDelete, setTaskToDelete] = useState<Task | null>(null);

  // Fetch tasks
  const { data: tasks = [], isLoading: tasksLoading } = useQuery({
    queryKey: ['tasks', statusFilter === 'all' ? undefined : statusFilter],
    queryFn: () => tasksService.list(statusFilter === 'all' ? undefined : statusFilter),
    refetchInterval: 5000, // Poll every 5 seconds for status updates
  });

  // Fetch agents for mapping agent names
  const { data: agents = [] } = useQuery({
    queryKey: ['agents'],
    queryFn: () => agentsService.list(),
  });

  const agentMap = useMemo(() => {
    return agents.reduce((acc, agent) => {
      acc[agent.id] = agent;
      return acc;
    }, {} as Record<string, Agent>);
  }, [agents]);

  // Filter tasks by search query
  const filteredTasks = useMemo(() => {
    if (!searchQuery) return tasks;
    const query = searchQuery.toLowerCase();
    return tasks.filter(task =>
      task.title.toLowerCase().includes(query) ||
      agentMap[task.agentId]?.name.toLowerCase().includes(query)
    );
  }, [tasks, searchQuery, agentMap]);

  // Handle actions
  const handleViewChat = (task: Task) => {
    navigate(`/chat?taskId=${task.id}`);
  };

  const handleCancel = async () => {
    if (!taskToCancel) return;
    try {
      await tasksService.cancel(taskToCancel.id);
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
    } finally {
      setTaskToCancel(null);
    }
  };

  const handleDelete = async () => {
    if (!taskToDelete) return;
    try {
      await tasksService.delete(taskToDelete.id);
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
    } finally {
      setTaskToDelete(null);
    }
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('tasks.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('tasks.subtitle')}</p>
        </div>
        <button
          onClick={() => navigate('/chat')}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
        >
          <span className="material-symbols-outlined text-xl">add</span>
          {t('tasks.newTask')}
        </button>
      </div>

      {/* Search and Filter */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">
            search
          </span>
          <input
            type="text"
            placeholder={t('tasks.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as TaskStatus | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('tasks.filter.all')}</option>
          <option value="running">{t('tasks.filter.running')}</option>
          <option value="completed">{t('tasks.filter.completed')}</option>
          <option value="failed">{t('tasks.filter.failed')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.name')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.agent')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.status')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.model')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.started')}
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.duration')}
              </th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                {t('tasks.columns.actions')}
              </th>
            </tr>
          </thead>
          <tbody>
            {tasksLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center">
                  <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">
                    sync
                  </span>
                </td>
              </tr>
            ) : filteredTasks.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-text-muted)]">
                  {t('tasks.empty')}
                </td>
              </tr>
            ) : (
              filteredTasks.map((task) => (
                <tr
                  key={task.id}
                  className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)] transition-colors"
                >
                  <td className="px-4 py-3">
                    <span className="text-[var(--color-text)] font-medium truncate block max-w-xs">
                      {task.title}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)]">
                    {agentMap[task.agentId]?.name || task.agentId}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={task.status} />
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">
                    {task.model?.replace('claude-', '').replace(/-\d+$/, '') || '-'}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">
                    {formatRelativeTime(task.startedAt || task.createdAt)}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm font-mono">
                    {formatDuration(task.startedAt, task.completedAt)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => handleViewChat(task)}
                        title={t('tasks.actions.viewChat')}
                        className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] rounded-lg transition-colors"
                      >
                        <span className="material-symbols-outlined text-xl">chat</span>
                      </button>
                      {task.status === 'running' && (
                        <button
                          onClick={() => setTaskToCancel(task)}
                          title={t('tasks.actions.cancel')}
                          className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                        >
                          <span className="material-symbols-outlined text-xl">stop_circle</span>
                        </button>
                      )}
                      <button
                        onClick={() => setTaskToDelete(task)}
                        title={t('tasks.actions.delete')}
                        className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors"
                      >
                        <span className="material-symbols-outlined text-xl">delete</span>
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Confirm Dialogs */}
      <ConfirmDialog
        isOpen={!!taskToCancel}
        title={t('tasks.actions.cancel')}
        message={t('tasks.confirmCancel')}
        onConfirm={handleCancel}
        onCancel={() => setTaskToCancel(null)}
      />
      <ConfirmDialog
        isOpen={!!taskToDelete}
        title={t('tasks.actions.delete')}
        message={t('tasks.confirmDelete')}
        onConfirm={handleDelete}
        onCancel={() => setTaskToDelete(null)}
      />
    </div>
  );
}
