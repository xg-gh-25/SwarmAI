import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  SearchBar,
  Button,
  Modal,
  SkeletonTable,
  ResizableTable,
  ResizableTableCell,
  Breadcrumb,
} from '../components/common';
import { FolderPickerModal } from '../components/workspace/FolderPickerModal';
import type { SwarmWorkspace, SwarmWorkspaceCreateRequest, SwarmWorkspaceUpdateRequest } from '../types';
import { swarmWorkspacesService } from '../services/swarmWorkspaces';

// Table column configuration
const getWorkspaceColumns = (t: (key: string) => string) => [
  { key: 'name', header: t('workspaces.table.name'), initialWidth: 200, minWidth: 150 },
  { key: 'filePath', header: t('workspaces.table.filePath'), initialWidth: 350, minWidth: 200 },
  { key: 'context', header: t('workspaces.table.context'), initialWidth: 250, minWidth: 150 },
  { key: 'updatedAt', header: t('common.label.updated'), initialWidth: 160, minWidth: 120 },
  { key: 'actions', header: t('workspaces.table.actions'), initialWidth: 120, minWidth: 100, align: 'right' as const },
];

// Format timestamp to readable date time
function formatDateTime(dateString: string): string {
  if (!dateString) return '-';
  const date = new Date(dateString);
  return date.toLocaleString();
}

// Truncate text with ellipsis
function truncateText(text: string, maxLength: number): string {
  if (!text) return '-';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
}

export default function WorkspacesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<SwarmWorkspace | null>(null);
  const [deletingWorkspace, setDeletingWorkspace] = useState<SwarmWorkspace | null>(null);

  // Get translated columns
  const WORKSPACE_COLUMNS = useMemo(() => getWorkspaceColumns(t), [t]);

  // Fetch workspaces using TanStack Query
  const {
    data: workspaces = [],
    isLoading,
    error,
  } = useQuery<SwarmWorkspace[]>({
    queryKey: ['swarmWorkspaces'],
    queryFn: swarmWorkspacesService.list,
  });

  // Filter workspaces based on search query
  const filteredWorkspaces = useMemo(() => {
    if (!searchQuery.trim()) return workspaces;
    const query = searchQuery.toLowerCase();
    return workspaces.filter(
      (workspace) =>
        workspace.name.toLowerCase().includes(query) ||
        workspace.filePath.toLowerCase().includes(query) ||
        workspace.context?.toLowerCase().includes(query)
    );
  }, [workspaces, searchQuery]);

  // Handle create workspace button click
  const handleCreateWorkspace = () => {
    setIsCreateModalOpen(true);
  };

  // Handle workspace created successfully
  const handleWorkspaceCreated = () => {
    setIsCreateModalOpen(false);
    // Invalidate query to refresh the list
    queryClient.invalidateQueries({ queryKey: ['swarmWorkspaces'] });
  };

  // Handle edit workspace button click
  const handleEditWorkspace = (workspace: SwarmWorkspace) => {
    setEditingWorkspace(workspace);
  };

  // Handle workspace updated successfully
  const handleWorkspaceUpdated = () => {
    setEditingWorkspace(null);
    // Invalidate query to refresh the list
    queryClient.invalidateQueries({ queryKey: ['swarmWorkspaces'] });
  };

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => swarmWorkspacesService.delete(id),
    onSuccess: () => {
      setDeletingWorkspace(null);
      queryClient.invalidateQueries({ queryKey: ['swarmWorkspaces'] });
    },
  });

  // Handle delete workspace button click
  const handleDeleteWorkspace = (workspace: SwarmWorkspace) => {
    setDeletingWorkspace(workspace);
  };

  // Handle delete confirmation
  const handleConfirmDelete = () => {
    if (deletingWorkspace) {
      deleteMutation.mutate(deletingWorkspace.id);
    }
  };

  return (
    <div className="p-8">
      <Breadcrumb currentPage={t('workspaces.title')} />

      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('workspaces.title')}</h1>
          <p className="text-[var(--color-text-muted)] mt-1">{t('workspaces.subtitle')}</p>
        </div>
        <Button icon="add" onClick={handleCreateWorkspace}>
          {t('workspaces.createWorkspace')}
        </Button>
      </div>

      {/* Toolbar */}
      <div className="mb-6">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t('workspaces.searchPlaceholder')}
          className="w-96"
        />
      </div>

      {/* Error State */}
      {error && (
        <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-4 mb-6">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-status-error">error</span>
            <span className="text-status-error">{t('workspaces.loadError')}</span>
          </div>
        </div>
      )}

      {/* Workspaces Table */}
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {isLoading ? (
          <SkeletonTable rows={5} columns={5} />
        ) : (
          <ResizableTable columns={WORKSPACE_COLUMNS}>
            {filteredWorkspaces.map((workspace) => (
              <tr
                key={workspace.id}
                className="border-b border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors"
              >
                <ResizableTableCell>
                  <div className="flex items-center gap-2">
                    {/* Workspace Icon */}
                    {workspace.icon && (
                      <span className="text-lg">{workspace.icon}</span>
                    )}
                    <span className="text-[var(--color-text)] font-medium">
                      {workspace.name}
                    </span>
                    {/* Default Workspace Badge */}
                    {workspace.isDefault && (
                      <span className="px-2 py-0.5 text-xs bg-[var(--color-primary)]/10 text-[var(--color-primary)] rounded-full">
                        {t('workspaces.default')}
                      </span>
                    )}
                  </div>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span
                    className="text-[var(--color-text-muted)] font-mono text-sm"
                    title={workspace.filePath}
                  >
                    {truncateText(workspace.filePath, 50)}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span
                    className="text-[var(--color-text-muted)]"
                    title={workspace.context}
                  >
                    {truncateText(workspace.context, 40)}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="text-[var(--color-text-muted)] text-sm">
                    {formatDateTime(workspace.updatedAt)}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell align="right">
                  <div className="flex items-center justify-end gap-1">
                    {/* Edit button */}
                    <button
                      onClick={() => handleEditWorkspace(workspace)}
                      className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
                      title={t('workspaces.editWorkspace')}
                    >
                      <span className="material-symbols-outlined text-lg">edit</span>
                    </button>
                    {/* Delete button - disabled for default workspace */}
                    {workspace.isDefault ? (
                      <button
                        disabled
                        className="p-1.5 rounded-lg text-[var(--color-text-muted)]/40 cursor-not-allowed"
                        title={t('workspaces.cannotDeleteDefault')}
                      >
                        <span className="material-symbols-outlined text-lg">delete</span>
                      </button>
                    ) : (
                      <button
                        onClick={() => handleDeleteWorkspace(workspace)}
                        className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                        title={t('workspaces.deleteWorkspace')}
                      >
                        <span className="material-symbols-outlined text-lg">delete</span>
                      </button>
                    )}
                  </div>
                </ResizableTableCell>
              </tr>
            ))}

            {/* Empty State */}
            {filteredWorkspaces.length === 0 && !isLoading && (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center">
                  <span className="material-symbols-outlined text-4xl text-[var(--color-text-muted)] mb-2">
                    folder_open
                  </span>
                  <p className="text-[var(--color-text-muted)]">
                    {searchQuery ? t('workspaces.noResults') : t('workspaces.noWorkspaces')}
                  </p>
                </td>
              </tr>
            )}
          </ResizableTable>
        )}
      </div>

      {/* Create Workspace Modal */}
      <Modal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        title={t('workspaces.createWorkspace')}
        size="lg"
      >
        <CreateWorkspaceForm
          onClose={() => setIsCreateModalOpen(false)}
          onSuccess={handleWorkspaceCreated}
        />
      </Modal>

      {/* Edit Workspace Modal */}
      <Modal
        isOpen={editingWorkspace !== null}
        onClose={() => setEditingWorkspace(null)}
        title={t('workspaces.editWorkspace')}
        size="lg"
      >
        {editingWorkspace && (
          <EditWorkspaceForm
            workspace={editingWorkspace}
            onClose={() => setEditingWorkspace(null)}
            onSuccess={handleWorkspaceUpdated}
          />
        )}
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deletingWorkspace !== null}
        onClose={() => setDeletingWorkspace(null)}
        title={t('workspaces.deleteConfirmTitle')}
        size="sm"
      >
        {deletingWorkspace && (
          <div className="space-y-4">
            <p className="text-[var(--color-text)]">
              {t('workspaces.deleteConfirmMessage', { name: deletingWorkspace.name })}
            </p>
            <p className="text-sm text-[var(--color-text-muted)]">
              {t('workspaces.deleteConfirmWarning')}
            </p>
            {deleteMutation.isError && (
              <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-3">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-status-error text-sm">error</span>
                  <span className="text-status-error text-sm">
                    {(deleteMutation.error as Error)?.message || t('workspaces.deleteError')}
                  </span>
                </div>
              </div>
            )}
            <div className="flex gap-3 pt-2">
              <Button
                variant="secondary"
                className="flex-1"
                onClick={() => setDeletingWorkspace(null)}
                disabled={deleteMutation.isPending}
              >
                {t('common.button.cancel')}
              </Button>
              <Button
                variant="danger"
                className="flex-1"
                onClick={handleConfirmDelete}
                isLoading={deleteMutation.isPending}
                disabled={deleteMutation.isPending}
              >
                {deleteMutation.isPending ? t('common.status.deleting') : t('common.button.delete')}
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// Create Workspace Form Component
function CreateWorkspaceForm({
  onClose,
  onSuccess,
}: {
  onClose: () => void;
  onSuccess: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [filePath, setFilePath] = useState('');
  const [context, setContext] = useState('');
  const [icon, setIcon] = useState('');
  const [isFolderPickerOpen, setIsFolderPickerOpen] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Create mutation
  const createMutation = useMutation({
    mutationFn: (data: SwarmWorkspaceCreateRequest) => swarmWorkspacesService.create(data),
    onSuccess: () => {
      onSuccess();
    },
    onError: (error: Error) => {
      setErrors({ submit: error.message });
    },
  });

  // Validate form fields
  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!name.trim()) {
      newErrors.name = t('workspaces.form.nameRequired');
    } else if (name.length > 100) {
      newErrors.name = t('workspaces.form.nameTooLong');
    }

    if (!filePath.trim()) {
      newErrors.filePath = t('workspaces.form.filePathRequired');
    }

    if (!context.trim()) {
      newErrors.context = t('workspaces.form.contextRequired');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle form submission
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm()) return;

    const data: SwarmWorkspaceCreateRequest = {
      name: name.trim(),
      filePath: filePath.trim(),
      context: context.trim(),
      icon: icon.trim() || undefined,
    };

    createMutation.mutate(data);
  };

  // Handle folder selection from picker
  const handleFolderSelect = (path: string) => {
    setFilePath(path);
    setIsFolderPickerOpen(false);
    // Clear file path error if it was set
    if (errors.filePath) {
      setErrors((prev) => ({ ...prev, filePath: '' }));
    }
  };

  return (
    <>
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Name Field */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('workspaces.form.name')} <span className="text-status-error">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (errors.name) setErrors((prev) => ({ ...prev, name: '' }));
            }}
            placeholder={t('workspaces.form.namePlaceholder')}
            className={`w-full px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary ${
              errors.name ? 'border-status-error' : 'border-[var(--color-border)]'
            }`}
            maxLength={100}
          />
          {errors.name && (
            <p className="mt-1 text-sm text-status-error">{errors.name}</p>
          )}
        </div>

        {/* File Path Field with Folder Picker */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('workspaces.form.filePath')} <span className="text-status-error">*</span>
          </label>
          <div className="flex gap-2">
            <input
              type="text"
              value={filePath}
              onChange={(e) => {
                setFilePath(e.target.value);
                if (errors.filePath) setErrors((prev) => ({ ...prev, filePath: '' }));
              }}
              placeholder={t('workspaces.form.filePathPlaceholder')}
              className={`flex-1 px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary font-mono text-sm ${
                errors.filePath ? 'border-status-error' : 'border-[var(--color-border)]'
              }`}
            />
            <Button
              type="button"
              variant="secondary"
              icon="folder_open"
              onClick={() => setIsFolderPickerOpen(true)}
            >
              {t('workspaces.form.browse')}
            </Button>
          </div>
          {errors.filePath && (
            <p className="mt-1 text-sm text-status-error">{errors.filePath}</p>
          )}
        </div>

        {/* Context Field */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('workspaces.form.context')} <span className="text-status-error">*</span>
          </label>
          <textarea
            value={context}
            onChange={(e) => {
              setContext(e.target.value);
              if (errors.context) setErrors((prev) => ({ ...prev, context: '' }));
            }}
            placeholder={t('workspaces.form.contextPlaceholder')}
            rows={3}
            className={`w-full px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary resize-none ${
              errors.context ? 'border-status-error' : 'border-[var(--color-border)]'
            }`}
          />
          {errors.context && (
            <p className="mt-1 text-sm text-status-error">{errors.context}</p>
          )}
        </div>

        {/* Icon Field (Optional) */}
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
            {t('workspaces.form.icon')} <span className="text-[var(--color-text-muted)]">({t('common.label.optional')})</span>
          </label>
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={icon}
              onChange={(e) => setIcon(e.target.value)}
              placeholder={t('workspaces.form.iconPlaceholder')}
              className="w-24 px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary text-center text-xl"
              maxLength={4}
            />
            {icon && (
              <span className="text-2xl">{icon}</span>
            )}
            <span className="text-sm text-[var(--color-text-muted)]">
              {t('workspaces.form.iconHint')}
            </span>
          </div>
        </div>

        {/* Submit Error */}
        {errors.submit && (
          <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-3">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-status-error text-sm">error</span>
              <span className="text-status-error text-sm">{errors.submit}</span>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="flex gap-3 pt-4">
          <Button
            type="button"
            variant="secondary"
            className="flex-1"
            onClick={onClose}
          >
            {t('common.button.cancel')}
          </Button>
          <Button
            type="submit"
            className="flex-1"
            isLoading={createMutation.isPending}
            disabled={createMutation.isPending}
          >
            {createMutation.isPending ? t('common.status.creating') : t('common.button.create')}
          </Button>
        </div>
      </form>

      {/* Folder Picker Modal */}
      <FolderPickerModal
        isOpen={isFolderPickerOpen}
        onClose={() => setIsFolderPickerOpen(false)}
        onSelect={handleFolderSelect}
        initialPath={filePath || undefined}
      />
    </>
  );
}

// Edit Workspace Form Component
function EditWorkspaceForm({
  workspace,
  onClose,
  onSuccess,
}: {
  workspace: SwarmWorkspace;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const { t } = useTranslation();
  // Pre-populate form with current workspace values
  const [name, setName] = useState(workspace.name);
  const [context, setContext] = useState(workspace.context);
  const [icon, setIcon] = useState(workspace.icon || '');
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: SwarmWorkspaceUpdateRequest) =>
      swarmWorkspacesService.update(workspace.id, data),
    onSuccess: () => {
      onSuccess();
    },
    onError: (error: Error) => {
      setErrors({ submit: error.message });
    },
  });

  // Validate form fields
  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!name.trim()) {
      newErrors.name = t('workspaces.form.nameRequired');
    } else if (name.length > 100) {
      newErrors.name = t('workspaces.form.nameTooLong');
    }

    if (!context.trim()) {
      newErrors.context = t('workspaces.form.contextRequired');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle form submission
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm()) return;

    const data: SwarmWorkspaceUpdateRequest = {
      name: name.trim(),
      context: context.trim(),
      icon: icon.trim() || undefined,
    };

    updateMutation.mutate(data);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Name Field */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
          {t('workspaces.form.name')} <span className="text-status-error">*</span>
        </label>
        <input
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (errors.name) setErrors((prev) => ({ ...prev, name: '' }));
          }}
          placeholder={t('workspaces.form.namePlaceholder')}
          className={`w-full px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary ${
            errors.name ? 'border-status-error' : 'border-[var(--color-border)]'
          }`}
          maxLength={100}
        />
        {errors.name && (
          <p className="mt-1 text-sm text-status-error">{errors.name}</p>
        )}
      </div>

      {/* File Path Field - Read Only */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
          {t('workspaces.form.filePath')}
        </label>
        <input
          type="text"
          value={workspace.filePath}
          readOnly
          disabled
          className="w-full px-4 py-2 bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg text-[var(--color-text-muted)] font-mono text-sm cursor-not-allowed"
          title={t('workspaces.form.filePathReadOnly')}
        />
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          {t('workspaces.form.filePathReadOnly')}
        </p>
      </div>

      {/* Context Field */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
          {t('workspaces.form.context')} <span className="text-status-error">*</span>
        </label>
        <textarea
          value={context}
          onChange={(e) => {
            setContext(e.target.value);
            if (errors.context) setErrors((prev) => ({ ...prev, context: '' }));
          }}
          placeholder={t('workspaces.form.contextPlaceholder')}
          rows={3}
          className={`w-full px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary resize-none ${
            errors.context ? 'border-status-error' : 'border-[var(--color-border)]'
          }`}
        />
        {errors.context && (
          <p className="mt-1 text-sm text-status-error">{errors.context}</p>
        )}
      </div>

      {/* Icon Field (Optional) */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">
          {t('workspaces.form.icon')} <span className="text-[var(--color-text-muted)]">({t('common.label.optional')})</span>
        </label>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={icon}
            onChange={(e) => setIcon(e.target.value)}
            placeholder={t('workspaces.form.iconPlaceholder')}
            className="w-24 px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary text-center text-xl"
            maxLength={4}
          />
          {icon && (
            <span className="text-2xl">{icon}</span>
          )}
          <span className="text-sm text-[var(--color-text-muted)]">
            {t('workspaces.form.iconHint')}
          </span>
        </div>
      </div>

      {/* Submit Error */}
      {errors.submit && (
        <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-3">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-status-error text-sm">error</span>
            <span className="text-status-error text-sm">{errors.submit}</span>
          </div>
        </div>
      )}

      {/* Action Buttons */}
      <div className="flex gap-3 pt-4">
        <Button
          type="button"
          variant="secondary"
          className="flex-1"
          onClick={onClose}
        >
          {t('common.button.cancel')}
        </Button>
        <Button
          type="submit"
          className="flex-1"
          isLoading={updateMutation.isPending}
          disabled={updateMutation.isPending}
        >
          {updateMutation.isPending ? t('common.status.saving') : t('common.button.save')}
        </Button>
      </div>
    </form>
  );
}
