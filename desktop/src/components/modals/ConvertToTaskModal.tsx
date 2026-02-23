import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation } from '@tanstack/react-query';
import clsx from 'clsx';
import Modal from '../common/Modal';
import Button from '../common/Button';
import { todosService } from '../../services/todos';
import { swarmWorkspacesService } from '../../services/swarmWorkspaces';
import type { ToDo } from '../../types/todo';

interface ConvertToTaskModalProps {
  isOpen: boolean;
  todo: ToDo;
  onClose: () => void;
  onConverted: () => void;
}

export default function ConvertToTaskModal({
  isOpen,
  todo,
  onClose,
  onConverted,
}: ConvertToTaskModalProps) {
  const { t } = useTranslation();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Fetch workspaces for suggestion
  const { data: workspaces = [] } = useQuery({
    queryKey: ['swarmWorkspaces'],
    queryFn: () => swarmWorkspacesService.list(false),
    enabled: isOpen,
  });

  const convertMutation = useMutation({
    mutationFn: () => todosService.convertToTask(todo.id),
    onSuccess: () => {
      setErrorMessage(null);
      onConverted();
    },
    onError: () => {
      setErrorMessage(t('signals.error.convertFailed'));
    },
  });

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('signals.convertToTask.title')} size="md">
      <div className="space-y-4">
        {errorMessage && (
          <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
            {errorMessage}
          </div>
        )}

        {/* Task Title */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-1">
            {t('signals.convertToTask.taskTitle')}
          </label>
          <input
            type="text"
            value={todo.title}
            readOnly
            className="w-full px-3 py-2 bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] opacity-70 cursor-default"
          />
        </div>

        {/* Workspace Suggestion */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-1">
            {t('signals.convertToTask.workspace')}
          </label>
          <div className="space-y-2">
            {workspaces.map((ws) => {
              const isSuggested = ws.id === todo.workspaceId;
              const isArchived = ws.isArchived;
              return (
                <div
                  key={ws.id}
                  className={clsx(
                    'flex items-center gap-3 p-3 rounded-lg border',
                    isArchived
                      ? 'border-[var(--color-border)] opacity-50 cursor-not-allowed'
                      : isSuggested
                        ? 'border-primary bg-primary/5'
                        : 'border-[var(--color-border)]'
                  )}
                >
                  <span className="text-lg">{ws.isDefault ? '🏠' : '📁'}</span>
                  <div className="flex-1">
                    <span className="text-[var(--color-text)] font-medium">{ws.name}</span>
                    {isSuggested && (
                      <span className="ml-2 px-2 py-0.5 text-xs bg-primary/20 text-primary rounded-full">
                        {t('signals.convertToTask.suggested')}
                      </span>
                    )}
                    {isArchived && (
                      <p className="text-xs text-[var(--color-text-muted)] mt-1">
                        {t('signals.convertToTask.archivedMessage')}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Actions */}
        <div className="flex gap-3 pt-2">
          <Button variant="secondary" className="flex-1" onClick={onClose}>
            {t('common.button.cancel')}
          </Button>
          <Button
            variant="primary"
            className="flex-1"
            onClick={() => convertMutation.mutate()}
            isLoading={convertMutation.isPending}
          >
            {t('signals.convertToTask.convert')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
