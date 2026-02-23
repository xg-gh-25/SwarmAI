import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { tasksService } from '../services/tasks';
import { agentsService } from '../services/agents';
import { ConfirmDialog, Breadcrumb } from '../components/common';
import type { Task, TaskStatus, Agent } from '../types';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

function formatRelativeTime(dateString: string | null, t: TFunction): string {
  if (!dateString) return '-';
  const date = new Date(dateString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return t('tasks.time.justNow');
  if (diffMins < 60) return t('tasks.time.minutesAgo', { count: diffMins });
  if (diffHours < 24) return t('tasks.time.hoursAgo', { count: diffHours });
  return t('tasks.time.daysAgo', { count: diffDays });
}

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

function ExecuteStatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation();
  const config: Record<TaskStatus, { color: string; icon: string; spin?: boolean }> = {
    draft: { color: 'bg-gray-500/20 text-gray-400', icon: 'edit_note' },
    wip: { color: 'bg-blue-500/20 text-blue-400', icon: 'sync', spin: true },
    blocked: { color: 'bg-red-500/20 text-red-400', icon: 'block' },
    completed: { color: 'bg-green-500/20 text-green-400', icon: 'check_circle' },
    cancelled: { color: 'bg-gray-500/20 text-gray-400', icon: 'cancel' },
  };
  const { color, icon, spin = false } = config[status] || config.draft;
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', color)}>
      <span className={clsx('material-symbols-outlined text-sm', spin && 'animate-spin')}>{icon}</span>
      {t(`execute.status.${status}`)}
    </span>
  );
}

export default function ExecutePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'all'>('all');
  const [taskToCancel, setTaskToCancel] = useState<Task | null>(null);
  const [taskToDelete, setTaskToDelete] = useState<Task | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Fetch tasks with workspace filter
  const { data: tasks = [], isLoading: tasksLoading } = useQuery({
    queryKey: ['tasks', statusFilter === 'all' ? undefined : statusFilter, undefined, workspaceId],
    queryFn: () => tasksService.list(statusFilter === 'all' ? undefined : statusFilter, undefined, workspaceId),
    refetchInterval: 5000,
  });

  // Fetch agents for mapping
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

  const filteredTasks = useMemo(() => {
    if (!searchQuery) return tasks;
    const query = searchQuery.toLowerCase();
    return tasks.filter(
      (task) =>
        task.title.toLowerCase().includes(query) ||
        agentMap[task.agentId]?.name.toLowerCase().includes(query)
    );
  }, [tasks, searchQuery, agentMap]);

  const handleViewChat = (task: Task) => {
    navigate(`/chat?taskId=${task.id}&taskMode=true`);
  };

  const handleCancel = async () => {
    if (!taskToCancel) return;
    try {
      await tasksService.cancel(taskToCancel.id);
      setStatusFilter('all');
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['runningTaskCount'] });
      setErrorMessage(null);
    } catch {
      setErrorMessage(t('execute.error.cancelFailed'));
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
      setErrorMessage(null);
    } catch {
      setErrorMessage(t('execute.error.deleteFailed'));
    } finally {
      setTaskToDelete(null);
    }
  };

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('execute.title')} />

      {/* Error Toast */}
      {errorMessage && (
        <div className="mb-4 p-4 bg-red-500/10 border border-red-500/30 rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-2 text-red-400">
            <span className="material-symbols-outlined">error</span>
            <span>{errorMessage}</span>
          </div>
          <button onClick={() => setErrorMessage(null)} className="p-1 text-red-400 hover:text-red-300 transition-colors">
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('execute.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('execute.subtitle')}</p>
        </div>
        <button
          onClick={() => navigate('/chat?taskMode=true')}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
        >
          <span className="material-symbols-outlined text-xl">add</span>
          {t('execute.newTask')}
        </button>
      </div>

      {/* Search and Filter */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('execute.search')}
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
          <option value="all">{t('execute.filter.all')}</option>
          <option value="draft">{t('execute.filter.draft')}</option>
          <option value="wip">{t('execute.filter.wip')}</option>
          <option value="blocked">{t('execute.filter.blocked')}</option>
          <option value="completed">{t('execute.filter.completed')}</option>
          <option value="cancelled">{t('execute.filter.cancelled')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.name')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.agent')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.status')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.model')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.started')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.duration')}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('execute.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {tasksLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center">
                  <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
                </td>
              </tr>
            ) : filteredTasks.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-[var(--color-text-muted)]">{t('execute.empty')}</td>
              </tr>
            ) : (
              filteredTasks.map((task) => (
                <tr key={task.id} className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)] transition-colors">
                  <td className="px-4 py-3">
                    <span className="text-[var(--color-text)] font-medium truncate block max-w-xs">{task.title}</span>
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)]">{agentMap[task.agentId]?.name || task.agentId}</td>
                  <td className="px-4 py-3"><ExecuteStatusBadge status={task.status} /></td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">{task.model?.replace('claude-', '').replace(/-\d+$/, '') || '-'}</td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">{formatRelativeTime(task.startedAt || task.createdAt, t)}</td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm font-mono">{formatDuration(task.startedAt, task.completedAt)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => handleViewChat(task)} title={t('execute.actions.viewChat')} className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] rounded-lg transition-colors">
                        <span className="material-symbols-outlined text-xl">chat</span>
                      </button>
                      {task.status === 'wip' && (
                        <button onClick={() => setTaskToCancel(task)} title={t('execute.actions.cancel')} className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors">
                          <span className="material-symbols-outlined text-xl">stop_circle</span>
                        </button>
                      )}
                      <button onClick={() => setTaskToDelete(task)} title={t('execute.actions.delete')} className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors">
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
        title={t('execute.actions.cancel')}
        message={t('execute.confirmCancel')}
        onConfirm={handleCancel}
        onClose={() => setTaskToCancel(null)}
      />
      <ConfirmDialog
        isOpen={!!taskToDelete}
        title={t('execute.actions.delete')}
        message={t('execute.confirmDelete')}
        onConfirm={handleDelete}
        onClose={() => setTaskToDelete(null)}
      />
    </div>
  );
}
