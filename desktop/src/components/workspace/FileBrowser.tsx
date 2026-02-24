import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import { workspaceService } from '../../services/workspace';
import type { WorkspaceFile } from '../../types';

interface FileBrowserProps {
  agentId: string;
  onFileSelect: (file: { path: string; name: string }) => void;
  className?: string;
  /** Optional custom base path for file browsing (e.g., from "work in a folder" selection) */
  basePath?: string;
}

// Format file size for display
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB'];
  let unitIndex = 0;
  let size = bytes;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }
  return `${size.toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`;
};

// Get icon for file type
const getFileIcon = (file: WorkspaceFile): string => {
  if (file.type === 'directory') return 'folder';

  const ext = file.name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'ts':
    case 'tsx':
    case 'js':
    case 'jsx':
      return 'javascript';
    case 'py':
      return 'code';
    case 'json':
    case 'yaml':
    case 'yml':
      return 'data_object';
    case 'md':
    case 'txt':
      return 'description';
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
    case 'svg':
      return 'image';
    case 'html':
    case 'css':
      return 'web';
    case 'sh':
    case 'bash':
      return 'terminal';
    default:
      return 'draft';
  }
};

// Get icon color for file type
const getFileIconColor = (file: WorkspaceFile): string => {
  if (file.type === 'directory') return 'text-yellow-400';

  const ext = file.name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'ts':
    case 'tsx':
      return 'text-blue-400';
    case 'js':
    case 'jsx':
      return 'text-yellow-300';
    case 'py':
      return 'text-green-400';
    case 'json':
      return 'text-yellow-500';
    case 'yaml':
    case 'yml':
      return 'text-purple-400';
    case 'md':
      return 'text-muted';
    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
    case 'svg':
      return 'text-pink-400';
    default:
      return 'text-muted';
  }
};

export function FileBrowser({ agentId, onFileSelect, className, basePath }: FileBrowserProps) {
  const [currentPath, setCurrentPath] = useState<string>('.');

  // Reset to root when basePath changes
  useEffect(() => {
    setCurrentPath('.');
  }, [basePath]);

  // Fetch files for current path
  const {
    data: fileList,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['workspace', agentId, currentPath, basePath],
    queryFn: () => workspaceService.listFiles(agentId, currentPath, basePath),
    enabled: !!agentId,
    staleTime: 30000, // Cache for 30 seconds
  });

  // Handle file/folder click
  const handleItemClick = (file: WorkspaceFile) => {
    if (file.type === 'directory') {
      // Navigate into directory
      const newPath = currentPath === '.' ? file.name : `${currentPath}/${file.name}`;
      setCurrentPath(newPath);
    } else {
      // Open file preview
      const filePath = currentPath === '.' ? file.name : `${currentPath}/${file.name}`;
      onFileSelect({ path: filePath, name: file.name });
    }
  };

  // Handle navigation to parent directory
  const handleNavigateUp = () => {
    if (fileList?.parentPath !== null && fileList?.parentPath !== undefined) {
      setCurrentPath(fileList.parentPath);
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
  };

  return (
    <div className={clsx('flex flex-col h-full', className)}>
      {/* Header with breadcrumb and refresh */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-dark-border bg-dark-card/50">
        {/* Breadcrumb navigation */}
        <div className="flex items-center gap-1 text-sm overflow-x-auto">
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

      {/* File list */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center h-32 text-muted">
            <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
            Loading...
          </div>
        ) : isError ? (
          <div className="flex flex-col items-center justify-center h-32 text-status-error px-4 text-center">
            <span className="material-symbols-outlined text-2xl mb-2">error</span>
            <span className="text-sm">
              {error instanceof Error ? error.message : 'Failed to load files'}
            </span>
            <button
              onClick={() => refetch()}
              className="mt-2 text-xs text-primary hover:underline"
            >
              Try again
            </button>
          </div>
        ) : !fileList || fileList.files.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-32 text-muted">
            <span className="material-symbols-outlined text-2xl mb-2">folder_off</span>
            <span className="text-sm">Empty directory</span>
          </div>
        ) : (
          <div className="divide-y divide-dark-border/50">
            {/* Parent directory link */}
            {fileList.parentPath !== null && (
              <button
                onClick={handleNavigateUp}
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-dark-hover transition-colors text-left"
              >
                <span className="material-symbols-outlined text-lg text-muted">
                  folder_open
                </span>
                <span className="text-muted text-sm">..</span>
              </button>
            )}

            {/* Files and directories */}
            {fileList.files.map((file) => (
              <button
                key={file.name}
                onClick={() => handleItemClick(file)}
                className="w-full flex items-center gap-3 px-3 py-2 hover:bg-dark-hover transition-colors text-left group"
              >
                <span
                  className={clsx(
                    'material-symbols-outlined text-lg',
                    getFileIconColor(file)
                  )}
                >
                  {getFileIcon(file)}
                </span>
                <span className="flex-1 text-sm text-white truncate group-hover:text-primary transition-colors">
                  {file.name}
                </span>
                <span className="text-xs text-muted flex-shrink-0">
                  {formatFileSize(file.size)}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default FileBrowser;
