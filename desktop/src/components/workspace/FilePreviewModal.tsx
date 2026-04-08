/**
 * File preview modal used by the ChatPage file browser (right sidebar).
 *
 * Uses the unified `classifyFileForPreview()` from fileUtils.ts to ensure
 * consistent file type routing across the app.
 *
 * - **image**: inline `<img>` with base64 data-URI
 * - **text**: syntax-highlighted code preview via CodePreview
 * - **unsupported**: metadata card with "Reveal in Finder" button
 */
import { useQuery } from '@tanstack/react-query';
import { useCallback } from 'react';
import Modal from '../common/Modal';
import CodePreview from './CodePreview';
import { workspaceService } from '../../services/workspace';
import { classifyFileForPreview } from '../../utils/fileUtils';

interface FilePreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  agentId: string;
  file: { path: string; name: string } | null;
  /** Optional custom base path for file reading (e.g., from "work in a folder" selection) */
  basePath?: string;
}

// Format file size for display
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let unitIndex = 0;
  let size = bytes;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }
  return `${size.toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`;
};

// Get file extension for display
const getFileExtension = (filename: string): string => {
  const ext = filename.split('.').pop()?.toUpperCase();
  return ext && ext !== filename.toUpperCase() ? ext : 'FILE';
};

export function FilePreviewModal({ isOpen, onClose, agentId, file, basePath }: FilePreviewModalProps) {
  // Classify using the single source of truth
  const previewType = file ? classifyFileForPreview(file.name) : 'unsupported';
  const canPreview = previewType === 'image' || previewType === 'text';

  // Fetch file content only for previewable files
  const {
    data: fileContent,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['workspace-file', agentId, file?.path, basePath],
    queryFn: () => workspaceService.readFile(agentId, file!.path, basePath),
    enabled: isOpen && !!file && !!agentId && canPreview,
    staleTime: 60000, // Cache for 1 minute
  });

  // Handle reveal file in system file manager (Finder/Explorer)
  const handleRevealInFinder = useCallback(async () => {
    if (!file || !basePath) return;

    // Construct full file path
    const fullPath = file.path === '.' ? basePath : `${basePath}/${file.path}`;

    // Check if running in Tauri environment
    if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
      try {
        const { revealItemInDir } = await import('@tauri-apps/plugin-opener');
        await revealItemInDir(fullPath);
      } catch (error) {
        console.error('Failed to reveal file in finder:', error);
      }
    } else {
      // Fallback for browser dev mode - just log the path
      console.log('Would reveal file:', fullPath);
      alert(`File path: ${fullPath}\n\n(Reveal not available in browser mode)`);
    }
  }, [file, basePath]);

  // Render content based on type
  const renderContent = () => {
    if (!file) return null;

    // For non-previewable files, show metadata only
    if (!canPreview) {
      const ext = getFileExtension(file.name);
      return (
        <div className="flex flex-col items-center justify-center h-64 text-[var(--color-text-muted)]">
          <div className="w-20 h-20 rounded-2xl bg-[var(--color-hover)] flex items-center justify-center mb-4">
            <span className="text-2xl font-bold text-primary">{ext}</span>
          </div>
          <span className="text-lg font-medium text-[var(--color-text)] mb-1">{file.name}</span>
          <span className="text-sm text-[var(--color-text-muted)] mb-4">
            This file type cannot be previewed
          </span>
          <button
            onClick={handleRevealInFinder}
            className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors"
          >
            <span className="material-symbols-outlined text-lg">folder_open</span>
            Reveal in Finder
          </button>
        </div>
      );
    }

    if (isLoading) {
      return (
        <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined animate-spin mr-2">
            progress_activity
          </span>
          Loading file...
        </div>
      );
    }

    if (isError) {
      // For errors, still show the reveal button
      const errorMessage = error instanceof Error ? error.message : 'Failed to load file';

      return (
        <div className="flex flex-col items-center justify-center h-64 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined text-5xl mb-4">error</span>
          <span className="text-lg font-medium mb-2">Cannot Load File</span>
          <span className="text-sm text-center mb-4 text-status-error">
            {errorMessage}
          </span>
          <button
            onClick={handleRevealInFinder}
            className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors"
          >
            <span className="material-symbols-outlined text-lg">folder_open</span>
            Reveal in Finder
          </button>
        </div>
      );
    }

    if (!fileContent) {
      return null;
    }

    // Reveal in Finder button - opens file location in system file manager
    const RevealButton = () => (
      <button
        onClick={handleRevealInFinder}
        className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors"
      >
        <span className="material-symbols-outlined text-lg">folder_open</span>
        Reveal in Finder
      </button>
    );

    // Image preview
    if (previewType === 'image' && fileContent.encoding === 'base64') {
      return (
        <div className="flex flex-col items-center">
          <img
            src={`data:${fileContent.mimeType};base64,${fileContent.content}`}
            alt={file.name}
            className="max-w-full max-h-[55vh] object-contain rounded-lg border border-[var(--color-border)]"
          />
          <div className="flex items-center justify-between w-full mt-4">
            <span className="text-sm text-[var(--color-text-muted)]">
              {fileContent.mimeType} - {formatFileSize(fileContent.size)}
            </span>
            <RevealButton />
          </div>
        </div>
      );
    }

    // Text/Code preview (default for previewable non-image files)
    return (
      <div className="flex flex-col">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-[var(--color-text-muted)]">
            {fileContent.mimeType} - {formatFileSize(fileContent.size)}
          </span>
          <RevealButton />
        </div>
        <CodePreview
          content={fileContent.encoding === 'utf-8' ? fileContent.content : '[Binary content]'}
          filename={file.name}
          showLineNumbers={true}
          className="max-h-[55vh]"
        />
      </div>
    );
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={file?.name || 'File Preview'}
      size="3xl"
    >
      {renderContent()}
    </Modal>
  );
}

export default FilePreviewModal;
