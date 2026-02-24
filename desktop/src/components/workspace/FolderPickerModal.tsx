import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { workspaceService } from '../../services/workspace';
import type { WorkspaceFile } from '../../types';

interface FolderPickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
  agentId: string;
  /** Initial path to start browsing from */
  initialPath?: string;
}

// Get icon color for folders
const getFolderIconColor = (isSelected: boolean): string => {
  return isSelected ? 'text-primary' : 'text-yellow-400';
};

export function FolderPickerModal({
  isOpen,
  onClose,
  onSelect,
  agentId,
  initialPath,
}: FolderPickerModalProps) {
  const { t } = useTranslation();
  const [currentPath, setCurrentPath] = useState<string>('.');
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setCurrentPath(initialPath || '.');
      setSelectedPath(null);
    }
  }, [isOpen, initialPath]);

  // Handle Escape key to close modal
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Fetch files for current path - browse from root (no basePath restriction)
  const {
    data: fileList,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['folderPicker', agentId, currentPath],
    queryFn: () => workspaceService.listFiles(agentId, currentPath),
    enabled: isOpen && !!agentId,
    staleTime: 30000,
  });

  // Filter to show only directories
  const directories = fileList?.files.filter((f) => f.type === 'directory') || [];

  // Handle directory double-click (navigate into)
  const handleNavigateInto = (dir: WorkspaceFile) => {
    const newPath = currentPath === '.' ? dir.name : `${currentPath}/${dir.name}`;
    setCurrentPath(newPath);
    setSelectedPath(null);
  };

  // Handle directory single-click (select)
  const handleSelectDir = (dir: WorkspaceFile) => {
    const dirPath = currentPath === '.' ? dir.name : `${currentPath}/${dir.name}`;
    setSelectedPath(dirPath);
  };

  // Handle navigation to parent directory
  const handleNavigateUp = () => {
    if (fileList?.parentPath !== null && fileList?.parentPath !== undefined) {
      setCurrentPath(fileList.parentPath);
      setSelectedPath(null);
    }
  };

  // Build breadcrumb parts
  const breadcrumbParts = currentPath === '.' ? [] : currentPath.split('/');

  // Handle breadcrumb click
  const handleBreadcrumbClick = (index: number) => {
    if (index === -1) {
      setCurrentPath('.');
    } else {
      setCurrentPath(breadcrumbParts.slice(0, index + 1).join('/'));
    }
    setSelectedPath(null);
  };

  // Get the full path to display
  const getDisplayPath = () => {
    if (selectedPath) {
      return selectedPath === '.' ? '/' : `/${selectedPath}`;
    }
    return currentPath === '.' ? '/' : `/${currentPath}`;
  };

  // Handle confirm selection
  const handleConfirm = () => {
    // Use selected subfolder if one is selected, otherwise use current path
    const pathToSelect = selectedPath || currentPath;
    // Convert relative path to absolute by prepending the base workspace path
    // The backend will resolve this relative to the agent's workspace or system root
    onSelect(pathToSelect === '.' ? '/' : `/${pathToSelect}`);
    onClose();
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-dark-card border border-dark-border rounded-xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-dark-border">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary">folder_open</span>
            <h2 className="font-semibold text-white">{t('chat.selectWorkFolder') || 'Select Work Folder'}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-muted hover:text-white transition-colors rounded"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* Breadcrumb */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-dark-border/50 bg-dark-hover/30">
          <div className="flex items-center gap-1 text-sm overflow-x-auto flex-1">
            <button
              onClick={() => handleBreadcrumbClick(-1)}
              className="text-muted hover:text-white transition-colors flex-shrink-0"
              title="Root"
            >
              <span className="material-symbols-outlined text-lg">home</span>
            </button>
            {breadcrumbParts.map((part, index) => (
              <div key={index} className="flex items-center flex-shrink-0">
                <span className="text-muted mx-1">/</span>
                <button
                  onClick={() => handleBreadcrumbClick(index)}
                  className={clsx(
                    'hover:text-white transition-colors truncate max-w-[100px]',
                    index === breadcrumbParts.length - 1 ? 'text-white' : 'text-muted'
                  )}
                  title={part}
                >
                  {part}
                </button>
              </div>
            ))}
          </div>

          {/* Refresh button */}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1 text-muted hover:text-white transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <span
              className={clsx(
                'material-symbols-outlined text-lg',
                isFetching && 'animate-spin'
              )}
            >
              refresh
            </span>
          </button>
        </div>

        {/* Directory list */}
        <div className="flex-1 overflow-y-auto min-h-[200px] max-h-[400px]">
          {!agentId ? (
            <div className="flex flex-col items-center justify-center h-32 text-muted px-4 text-center">
              <span className="material-symbols-outlined text-2xl mb-2">person_off</span>
              <span className="text-sm">{t('chat.noAgent')}</span>
            </div>
          ) : isLoading ? (
            <div className="flex items-center justify-center h-32 text-muted">
              <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
              Loading...
            </div>
          ) : isError ? (
            <div className="flex flex-col items-center justify-center h-32 text-status-error px-4 text-center">
              <span className="material-symbols-outlined text-2xl mb-2">error</span>
              <span className="text-sm">
                {error instanceof Error ? error.message : 'Failed to load directories'}
              </span>
              <button
                onClick={() => refetch()}
                className="mt-2 text-xs text-primary hover:underline"
              >
                Try again
              </button>
            </div>
          ) : (
            <div className="divide-y divide-dark-border/50">
              {/* Parent directory link */}
              {fileList?.parentPath != null && (
                <button
                  onClick={handleNavigateUp}
                  className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-dark-hover transition-colors text-left"
                >
                  <span className="material-symbols-outlined text-lg text-muted">
                    folder_open
                  </span>
                  <span className="text-muted text-sm">..</span>
                </button>
              )}

              {/* Directories only */}
              {directories.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-muted">
                  <span className="material-symbols-outlined text-2xl mb-2">folder_off</span>
                  <span className="text-sm">No subdirectories</span>
                </div>
              ) : (
                directories.map((dir) => {
                  const dirPath = currentPath === '.' ? dir.name : `${currentPath}/${dir.name}`;
                  const isSelected = selectedPath === dirPath;
                  return (
                    <button
                      key={dir.name}
                      onClick={() => handleSelectDir(dir)}
                      onDoubleClick={() => handleNavigateInto(dir)}
                      className={clsx(
                        'w-full flex items-center gap-3 px-4 py-2.5 transition-colors text-left',
                        isSelected
                          ? 'bg-primary/20 border-l-2 border-primary'
                          : 'hover:bg-dark-hover border-l-2 border-transparent'
                      )}
                    >
                      <span
                        className={clsx(
                          'material-symbols-outlined text-lg',
                          getFolderIconColor(isSelected)
                        )}
                      >
                        folder
                      </span>
                      <span
                        className={clsx(
                          'flex-1 text-sm truncate',
                          isSelected ? 'text-primary font-medium' : 'text-white'
                        )}
                      >
                        {dir.name}
                      </span>
                      <span className="material-symbols-outlined text-muted text-sm">
                        chevron_right
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>

        {/* Footer with selected path and actions */}
        <div className="border-t border-dark-border px-4 py-3">
          {/* Selected path display */}
          <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-dark-hover/50 rounded-lg">
            <span className="material-symbols-outlined text-primary text-sm">folder</span>
            <span className="text-sm text-muted">{t('chat.selectedFolder') || 'Selected'}:</span>
            <span className="text-sm text-white font-medium truncate flex-1">
              {getDisplayPath()}
            </span>
          </div>

          {/* Action buttons */}
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-muted hover:text-white transition-colors"
            >
              {t('common.button.cancel')}
            </button>
            <button
              onClick={handleConfirm}
              className="px-4 py-2 text-sm bg-primary hover:bg-primary-hover text-white rounded-lg transition-colors"
            >
              {t('chat.selectThisFolder') || 'Select This Folder'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default FolderPickerModal;
