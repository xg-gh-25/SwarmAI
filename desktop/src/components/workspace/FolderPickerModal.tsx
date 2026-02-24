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
  initialPath,
}: FolderPickerModalProps) {
  const { t } = useTranslation();
  // Current browsing path (absolute path from server)
  const [currentPath, setCurrentPath] = useState<string>('.');
  // Selected subfolder within current path (null = select current path)
  const [selectedSubfolder, setSelectedSubfolder] = useState<string | null>(null);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setCurrentPath(initialPath || '.');
      setSelectedSubfolder(null);
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

  // Fetch directories using the browse API
  const {
    data: fileList,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['folderPicker', currentPath],
    queryFn: () => workspaceService.browseFilesystem(currentPath),
    enabled: isOpen,
    staleTime: 30000,
  });

  // Filter to show only directories
  const directories = fileList?.files.filter((f) => f.type === 'directory') || [];

  // Handle directory double-click (navigate into)
  const handleNavigateInto = (dir: WorkspaceFile) => {
    // fileList.currentPath is the absolute path, append dir.name
    const newPath = fileList?.currentPath
      ? `${fileList.currentPath}/${dir.name}`
      : dir.name;
    setCurrentPath(newPath);
    setSelectedSubfolder(null);
  };

  // Handle directory single-click (select)
  const handleSelectDir = (dir: WorkspaceFile) => {
    setSelectedSubfolder(dir.name);
  };

  // Handle navigation to parent directory
  const handleNavigateUp = () => {
    if (fileList?.parentPath) {
      setCurrentPath(fileList.parentPath);
      setSelectedSubfolder(null);
    }
  };

  // Build breadcrumb parts from absolute path
  const getBreadcrumbParts = () => {
    const absPath = fileList?.currentPath || '';
    if (!absPath || absPath === '/') return [];
    // Split by / and filter empty parts
    return absPath.split('/').filter(Boolean);
  };

  const breadcrumbParts = getBreadcrumbParts();

  // Handle breadcrumb click - navigate to that path level
  const handleBreadcrumbClick = (index: number) => {
    if (index === -1) {
      // Home - go to root/home
      setCurrentPath('.');
    } else {
      // Build path up to this index
      const newPath = '/' + breadcrumbParts.slice(0, index + 1).join('/');
      setCurrentPath(newPath);
    }
    setSelectedSubfolder(null);
  };

  // Get the path that will be selected
  const getSelectedPath = () => {
    const basePath = fileList?.currentPath || '/';
    if (selectedSubfolder) {
      return `${basePath}/${selectedSubfolder}`;
    }
    return basePath;
  };

  // Handle confirm selection
  const handleConfirm = () => {
    const path = getSelectedPath();
    onSelect(path);
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
      <div className="relative bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary">folder_open</span>
            <h2 className="font-semibold text-[var(--color-text)]">{t('chat.selectWorkFolder') || 'Select Work Folder'}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors rounded"
            aria-label="Close"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* Breadcrumb */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--color-border)]/50 bg-[var(--color-hover)]/30">
          <div className="flex items-center gap-1 text-sm overflow-x-auto flex-1">
            <button
              onClick={() => handleBreadcrumbClick(-1)}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors flex-shrink-0"
              title="Home"
            >
              <span className="material-symbols-outlined text-lg">home</span>
            </button>
            {breadcrumbParts.map((part, index) => (
              <div key={index} className="flex items-center flex-shrink-0">
                <span className="text-[var(--color-text-muted)] mx-1">/</span>
                <button
                  onClick={() => handleBreadcrumbClick(index)}
                  className={clsx(
                    'hover:text-[var(--color-text)] transition-colors truncate max-w-[100px]',
                    index === breadcrumbParts.length - 1 ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'
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
            className="p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors disabled:opacity-50"
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
          {isLoading ? (
            <div className="flex items-center justify-center h-32 text-[var(--color-text-muted)]">
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
            <div className="divide-y divide-[var(--color-border)]/50">
              {/* Parent directory link */}
              {fileList?.parentPath != null && (
                <button
                  onClick={handleNavigateUp}
                  className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-[var(--color-hover)] transition-colors text-left"
                >
                  <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
                    folder_open
                  </span>
                  <span className="text-[var(--color-text-muted)] text-sm">..</span>
                </button>
              )}

              {/* Directories only */}
              {directories.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined text-2xl mb-2">folder_off</span>
                  <span className="text-sm">No subdirectories</span>
                </div>
              ) : (
                directories.map((dir) => {
                  const isSelected = selectedSubfolder === dir.name;
                  return (
                    <button
                      key={dir.name}
                      onClick={() => handleSelectDir(dir)}
                      onDoubleClick={() => handleNavigateInto(dir)}
                      className={clsx(
                        'w-full flex items-center gap-3 px-4 py-2.5 transition-colors text-left',
                        isSelected
                          ? 'bg-primary/20 border-l-2 border-primary'
                          : 'hover:bg-[var(--color-hover)] border-l-2 border-transparent'
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
                          isSelected ? 'text-primary font-medium' : 'text-[var(--color-text)]'
                        )}
                      >
                        {dir.name}
                      </span>
                      <span className="material-symbols-outlined text-[var(--color-text-muted)] text-sm">
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
        <div className="border-t border-[var(--color-border)] px-4 py-3">
          {/* Selected path display */}
          <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-[var(--color-hover)]/50 rounded-lg">
            <span className="material-symbols-outlined text-primary text-sm">folder</span>
            <span className="text-sm text-[var(--color-text-muted)]">{t('chat.selectedFolder') || 'Selected'}:</span>
            <span className="text-sm text-[var(--color-text)] font-medium truncate flex-1">
              {getSelectedPath()}
            </span>
          </div>

          {/* Action buttons */}
          <div className="flex items-center justify-end gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
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
