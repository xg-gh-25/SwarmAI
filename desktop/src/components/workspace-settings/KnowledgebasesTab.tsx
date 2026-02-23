import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { workspaceConfigService } from '../../services/workspaceConfig';
import Button from '../common/Button';
import type { WorkspaceKnowledgebaseConfig } from '../../types/workspace-config';

interface KnowledgebasesTabProps {
  workspaceId: string;
}

interface KbFormData {
  sourceType: string;
  sourcePath: string;
  displayName: string;
}

const SOURCE_TYPES = ['local_file', 'url', 'indexed_document', 'context_file', 'vector_index'];

const EMPTY_FORM: KbFormData = { sourceType: 'local_file', sourcePath: '', displayName: '' };

export default function KnowledgebasesTab({ workspaceId }: KnowledgebasesTabProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formData, setFormData] = useState<KbFormData>(EMPTY_FORM);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const { data: knowledgebases = [], isLoading } = useQuery({
    queryKey: ['workspaceKnowledgebases', workspaceId],
    queryFn: () => workspaceConfigService.getKnowledgebases(workspaceId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['workspaceKnowledgebases', workspaceId] });
  };

  const addMutation = useMutation({
    mutationFn: (data: Partial<WorkspaceKnowledgebaseConfig>) =>
      workspaceConfigService.addKnowledgebase(workspaceId, data),
    onSuccess: () => {
      invalidate();
      resetForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ kbId, data }: { kbId: string; data: Partial<WorkspaceKnowledgebaseConfig> }) =>
      workspaceConfigService.updateKnowledgebase(workspaceId, kbId, data),
    onSuccess: () => {
      invalidate();
      resetForm();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (kbId: string) =>
      workspaceConfigService.deleteKnowledgebase(workspaceId, kbId),
    onSuccess: () => {
      invalidate();
      setDeleteConfirm(null);
    },
  });

  const resetForm = () => {
    setShowForm(false);
    setEditingId(null);
    setFormData(EMPTY_FORM);
  };

  const handleEdit = (kb: WorkspaceKnowledgebaseConfig) => {
    setEditingId(kb.id);
    setFormData({
      sourceType: kb.sourceType,
      sourcePath: kb.sourcePath,
      displayName: kb.displayName,
    });
    setShowForm(true);
  };

  const handleSubmit = () => {
    const payload: Partial<WorkspaceKnowledgebaseConfig> = {
      sourceType: formData.sourceType,
      sourcePath: formData.sourcePath,
      displayName: formData.displayName,
    };

    if (editingId) {
      updateMutation.mutate({ kbId: editingId, data: payload });
    } else {
      addMutation.mutate(payload);
    }
  };

  const isFormValid = formData.sourcePath.trim() !== '' && formData.displayName.trim() !== '';
  const isSaving = addMutation.isPending || updateMutation.isPending;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-[var(--color-text-muted)]">
        {t('common.loading')}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm text-[var(--color-text-muted)]">
          {t('settings.knowledgebases.description')}
        </p>
        {!showForm && (
          <Button
            variant="secondary"
            size="sm"
            icon="add"
            onClick={() => { setFormData(EMPTY_FORM); setShowForm(true); }}
          >
            {t('settings.knowledgebases.add')}
          </Button>
        )}
      </div>

      {/* Add/Edit Form */}
      {showForm && (
        <div className="p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] space-y-3">
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
              {t('settings.knowledgebases.displayName')}
            </label>
            <input
              type="text"
              value={formData.displayName}
              onChange={(e) => setFormData((f) => ({ ...f, displayName: e.target.value }))}
              placeholder={t('settings.knowledgebases.displayNamePlaceholder')}
              className="w-full px-3 py-2 text-sm bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
              {t('settings.knowledgebases.sourceType')}
            </label>
            <select
              value={formData.sourceType}
              onChange={(e) => setFormData((f) => ({ ...f, sourceType: e.target.value }))}
              className="w-full px-3 py-2 text-sm bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              {SOURCE_TYPES.map((st) => (
                <option key={st} value={st}>
                  {t(`settings.knowledgebases.types.${st}`)}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-[var(--color-text-muted)] mb-1">
              {t('settings.knowledgebases.sourcePath')}
            </label>
            <input
              type="text"
              value={formData.sourcePath}
              onChange={(e) => setFormData((f) => ({ ...f, sourcePath: e.target.value }))}
              placeholder={t('settings.knowledgebases.sourcePathPlaceholder')}
              className="w-full px-3 py-2 text-sm bg-[var(--color-input-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button variant="secondary" size="sm" onClick={resetForm}>
              {t('common.button.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              disabled={!isFormValid}
              isLoading={isSaving}
            >
              {editingId ? t('common.button.save') : t('settings.knowledgebases.add')}
            </Button>
          </div>
        </div>
      )}

      {/* Knowledgebase List */}
      {knowledgebases.length === 0 && !showForm ? (
        <div className="text-center py-8 text-[var(--color-text-muted)]">
          {t('settings.knowledgebases.empty')}
        </div>
      ) : (
        knowledgebases.map((kb) => (
          <div
            key={kb.id}
            className={clsx(
              'flex items-center justify-between p-3 rounded-lg border',
              'border-[var(--color-border)] bg-[var(--color-card)]'
            )}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
                menu_book
              </span>
              <div className="min-w-0">
                <span className="text-sm font-medium text-[var(--color-text)] truncate block">
                  {kb.displayName}
                </span>
                <span className="text-xs text-[var(--color-text-muted)]">
                  {kb.sourceType} · {kb.sourcePath}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => handleEdit(kb)}
                className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                title={t('common.button.edit')}
              >
                <span className="material-symbols-outlined text-lg">edit</span>
              </button>
              {deleteConfirm === kb.id ? (
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => deleteMutation.mutate(kb.id)}
                    className="px-2 py-1 text-xs rounded bg-status-error/20 text-status-error hover:bg-status-error/30 transition-colors"
                  >
                    {t('common.button.confirm')}
                  </button>
                  <button
                    onClick={() => setDeleteConfirm(null)}
                    className="px-2 py-1 text-xs rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors"
                  >
                    {t('common.button.cancel')}
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setDeleteConfirm(kb.id)}
                  className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                  title={t('common.button.delete')}
                >
                  <span className="material-symbols-outlined text-lg">delete</span>
                </button>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );
}
