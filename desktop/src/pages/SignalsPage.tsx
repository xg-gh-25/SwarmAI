import { useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { todosService } from '../services/todos';
import { Breadcrumb, ConfirmDialog } from '../components/common';
import type { ToDo, ToDoStatus, Priority } from '../types/todo';
import ConvertToTaskModal from '../components/modals/ConvertToTaskModal';
import { useWorkspaceId } from '../hooks/useWorkspaceId';

function PriorityBadge({ priority }: { priority: Priority }) {
  const { t } = useTranslation();
  const config: Record<Priority, { color: string; icon: string }> = {
    high: { color: 'bg-red-500/20 text-red-400', icon: 'priority_high' },
    medium: { color: 'bg-yellow-500/20 text-yellow-400', icon: 'drag_handle' },
    low: { color: 'bg-blue-500/20 text-blue-400', icon: 'arrow_downward' },
    none: { color: 'bg-gray-500/20 text-gray-400', icon: 'remove' },
  };
  const { color, icon } = config[priority] || config.none;
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', color)}>
      <span className="material-symbols-outlined text-sm">{icon}</span>
      {t(`signals.priority.${priority}`)}
    </span>
  );
}

function StatusBadge({ status }: { status: ToDoStatus }) {
  const { t } = useTranslation();
  const config: Record<ToDoStatus, { color: string; icon: string }> = {
    pending: { color: 'bg-yellow-500/20 text-yellow-400', icon: 'schedule' },
    overdue: { color: 'bg-red-500/20 text-red-400', icon: 'warning' },
    inDiscussion: { color: 'bg-blue-500/20 text-blue-400', icon: 'forum' },
    handled: { color: 'bg-green-500/20 text-green-400', icon: 'check_circle' },
    cancelled: { color: 'bg-gray-500/20 text-gray-400', icon: 'cancel' },
    deleted: { color: 'bg-gray-500/20 text-gray-400', icon: 'delete' },
  };
  const { color, icon } = config[status] || config.pending;
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium', color)}>
      <span className="material-symbols-outlined text-sm">{icon}</span>
      {t(`signals.status.${status}`)}
    </span>
  );
}

function formatDate(dateString?: string): string {
  if (!dateString) return '-';
  return new Date(dateString).toLocaleDateString();
}

export default function SignalsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<ToDoStatus | 'all'>('all');
  const [priorityFilter, setPriorityFilter] = useState<Priority | 'all'>('all');
  const [todoToDelete, setTodoToDelete] = useState<ToDo | null>(null);
  const [todoToConvert, setTodoToConvert] = useState<ToDo | null>(null);
  const [editingTodo, setEditingTodo] = useState<ToDo | null>(null);
  const [showQuickCapture, setShowQuickCapture] = useState(false);
  const [quickCaptureTitle, setQuickCaptureTitle] = useState('');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Fetch todos
  const { data: todos = [], isLoading } = useQuery({
    queryKey: ['todos', workspaceId, statusFilter === 'all' ? undefined : statusFilter],
    queryFn: () => todosService.list(workspaceId, statusFilter === 'all' ? undefined : statusFilter),
    refetchInterval: 10000,
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => todosService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      setTodoToDelete(null);
      setErrorMessage(null);
    },
    onError: () => {
      setErrorMessage(t('signals.error.deleteFailed'));
      setTodoToDelete(null);
    },
  });

  // Create mutation (quick capture)
  const createMutation = useMutation({
    mutationFn: (title: string) => todosService.create({ title, workspaceId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      setQuickCaptureTitle('');
      setShowQuickCapture(false);
    },
    onError: () => {
      setErrorMessage(t('signals.error.createFailed'));
    },
  });

  // Update mutation (inline edit)
  const updateMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      todosService.update(id, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['todos'] });
      setEditingTodo(null);
    },
    onError: () => {
      setErrorMessage(t('signals.error.updateFailed'));
    },
  });

  // Filter todos
  const filteredTodos = useMemo(() => {
    let result = todos;
    if (priorityFilter !== 'all') {
      result = result.filter((todo) => todo.priority === priorityFilter);
    }
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (todo) =>
          todo.title.toLowerCase().includes(query) ||
          todo.description?.toLowerCase().includes(query)
      );
    }
    return result;
  }, [todos, priorityFilter, searchQuery]);

  const handleQuickCapture = useCallback(() => {
    if (quickCaptureTitle.trim()) {
      createMutation.mutate(quickCaptureTitle.trim());
    }
  }, [quickCaptureTitle, createMutation]);

  return (
    <div className="flex-1 p-6 overflow-auto">
      <Breadcrumb currentPage={t('signals.title')} />

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
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('signals.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('signals.subtitle')}</p>
        </div>
        <button
          onClick={() => setShowQuickCapture(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
        >
          <span className="material-symbols-outlined text-xl">add</span>
          {t('signals.quickCapture')}
        </button>
      </div>

      {/* Quick Capture Inline Form */}
      {showQuickCapture && (
        <div className="mb-6 p-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg flex items-center gap-3">
          <input
            type="text"
            placeholder={t('signals.quickCapturePlaceholder')}
            value={quickCaptureTitle}
            onChange={(e) => setQuickCaptureTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleQuickCapture()}
            autoFocus
            className="flex-1 px-3 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
          <button onClick={handleQuickCapture} disabled={!quickCaptureTitle.trim()} className="px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
            {t('signals.add')}
          </button>
          <button onClick={() => { setShowQuickCapture(false); setQuickCaptureTitle(''); }} className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors">
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>
      )}

      {/* Search and Filters */}
      <div className="flex items-center gap-4 mb-6">
        <div className="relative flex-1 max-w-md">
          <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]">search</span>
          <input
            type="text"
            placeholder={t('signals.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-2 focus:ring-primary/50"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ToDoStatus | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('signals.filter.allStatuses')}</option>
          <option value="pending">{t('signals.filter.pending')}</option>
          <option value="overdue">{t('signals.filter.overdue')}</option>
          <option value="inDiscussion">{t('signals.filter.inDiscussion')}</option>
          <option value="handled">{t('signals.filter.handled')}</option>
          <option value="cancelled">{t('signals.filter.cancelled')}</option>
        </select>
        <select
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value as Priority | 'all')}
          className="px-4 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
        >
          <option value="all">{t('signals.filter.allPriorities')}</option>
          <option value="high">{t('signals.filter.high')}</option>
          <option value="medium">{t('signals.filter.medium')}</option>
          <option value="low">{t('signals.filter.low')}</option>
          <option value="none">{t('signals.filter.none')}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-[var(--color-card)] rounded-xl border border-[var(--color-border)] overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.title')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.source')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.status')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.priority')}</th>
              <th className="px-4 py-3 text-left text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.dueDate')}</th>
              <th className="px-4 py-3 text-right text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">{t('signals.columns.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center">
                  <span className="material-symbols-outlined animate-spin text-2xl text-[var(--color-text-muted)]">sync</span>
                </td>
              </tr>
            ) : filteredTodos.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-[var(--color-text-muted)]">{t('signals.empty')}</td>
              </tr>
            ) : (
              filteredTodos.map((todo) => (
                <tr key={todo.id} className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-hover)] transition-colors">
                  <td className="px-4 py-3">
                    {editingTodo?.id === todo.id ? (
                      <input
                        type="text"
                        defaultValue={todo.title}
                        autoFocus
                        onBlur={(e) => updateMutation.mutate({ id: todo.id, title: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') updateMutation.mutate({ id: todo.id, title: (e.target as HTMLInputElement).value });
                          if (e.key === 'Escape') setEditingTodo(null);
                        }}
                        className="w-full px-2 py-1 bg-[var(--color-input-bg)] border border-primary rounded text-[var(--color-text)] focus:outline-none"
                      />
                    ) : (
                      <span className="text-[var(--color-text)] font-medium truncate block max-w-xs">{todo.title}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm capitalize">{todo.sourceType}</td>
                  <td className="px-4 py-3"><StatusBadge status={todo.status} /></td>
                  <td className="px-4 py-3"><PriorityBadge priority={todo.priority} /></td>
                  <td className="px-4 py-3 text-[var(--color-text-muted)] text-sm">{formatDate(todo.dueDate)}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => setEditingTodo(todo)} title={t('signals.actions.edit')} className="p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] rounded-lg transition-colors">
                        <span className="material-symbols-outlined text-xl">edit</span>
                      </button>
                      <button onClick={() => setTodoToConvert(todo)} title={t('signals.actions.convertToTask')} className="p-2 text-[var(--color-text-muted)] hover:text-primary hover:bg-primary/10 rounded-lg transition-colors">
                        <span className="material-symbols-outlined text-xl">task_alt</span>
                      </button>
                      <button onClick={() => setTodoToDelete(todo)} title={t('signals.actions.delete')} className="p-2 text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-400/10 rounded-lg transition-colors">
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

      {/* Confirm Delete Dialog */}
      <ConfirmDialog
        isOpen={!!todoToDelete}
        title={t('signals.actions.delete')}
        message={t('signals.confirmDelete')}
        onConfirm={() => todoToDelete && deleteMutation.mutate(todoToDelete.id)}
        onClose={() => setTodoToDelete(null)}
      />

      {/* Convert to Task Modal */}
      {todoToConvert && (
        <ConvertToTaskModal
          isOpen={!!todoToConvert}
          todo={todoToConvert}
          onClose={() => setTodoToConvert(null)}
          onConverted={() => {
            queryClient.invalidateQueries({ queryKey: ['todos'] });
            setTodoToConvert(null);
          }}
        />
      )}
    </div>
  );
}
