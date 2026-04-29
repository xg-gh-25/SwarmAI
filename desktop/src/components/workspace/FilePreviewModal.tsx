/**
 * File preview modal used by the ChatPage file browser (right sidebar).
 *
 * Uses `classifyFileForViewer()` from the unified FileViewer type system
 * for consistent file type routing across the app.
 *
 * - **image**: inline `<img>` with base64 data-URI
 * - **text/markdown/svg/csv/html**: syntax-highlighted code preview via CodePreview
 * - **pdf**: inline PDF preview via react-pdf (lazy-loaded)
 * - **video/audio**: native `<video>` / `<audio>` with controls
 * - **unsupported**: metadata card with Hive-aware actions
 */
import { useQuery } from '@tanstack/react-query';
import { useCallback, lazy, Suspense } from 'react';
import Modal from '../common/Modal';
import CodePreview from './CodePreview';
import { workspaceService } from '../../services/workspace';
import { classifyFileForViewer, getFileTypeInfo, isBinaryType } from '../file-viewer/utils/fileViewTypes';
import type { FileViewType } from '../file-viewer/utils/fileViewTypes';

const PdfRenderer = lazy(() => import('../file-viewer/renderers/PdfRenderer'));

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


export function FilePreviewModal({ isOpen, onClose, agentId, file, basePath }: FilePreviewModalProps) {
  // Classify using the unified FileViewer type system
  const viewType: FileViewType = file ? classifyFileForViewer(file.name) : 'unsupported';
  const typeInfo = file ? getFileTypeInfo(file.name) : null;
  const canPreview = !isBinaryType(viewType) || viewType === 'image' || viewType === 'pdf';

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

  // Render content based on unified FileViewType
  const renderContent = () => {
    if (!file) return null;

    // Reveal in Finder button — Hive-aware (hidden when no Tauri)
    const RevealButton = () => (
      <button
        onClick={handleRevealInFinder}
        className="flex items-center gap-2 px-4 py-2 bg-primary hover:bg-primary-hover text-[var(--color-text)] rounded-lg transition-colors"
      >
        <span className="material-symbols-outlined text-lg">folder_open</span>
        Reveal in Finder
      </button>
    );

    // For unsupported files (Office, archives, executables, etc.) — friendly info card
    if (!canPreview) {
      const info = typeInfo ?? { label: 'File', icon: 'insert_drive_file' };
      return (
        <div className="flex flex-col items-center justify-center h-64 text-[var(--color-text-muted)]">
          <div className="w-20 h-20 rounded-2xl bg-[var(--color-hover)] flex items-center justify-center mb-4">
            <span className="material-symbols-outlined text-4xl text-primary">{info.icon}</span>
          </div>
          <span className="text-lg font-medium text-[var(--color-text)] mb-1">{file.name}</span>
          <span className="text-sm text-[var(--color-text-muted)] mb-4">
            {info.label} — can&apos;t be previewed in Swarm yet
          </span>
          <RevealButton />
        </div>
      );
    }

    if (isLoading) {
      return (
        <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
          Loading file...
        </div>
      );
    }

    if (isError) {
      const errorMessage = error instanceof Error ? error.message : 'Failed to load file';
      return (
        <div className="flex flex-col items-center justify-center h-64 text-[var(--color-text-muted)]">
          <span className="material-symbols-outlined text-5xl mb-4">error</span>
          <span className="text-lg font-medium mb-2">Cannot Load File</span>
          <span className="text-sm text-center mb-4 text-status-error">{errorMessage}</span>
          <RevealButton />
        </div>
      );
    }

    if (!fileContent) return null;

    const infoBar = (
      <div className="flex items-center justify-between w-full mt-4">
        <span className="text-sm text-[var(--color-text-muted)]">
          {typeInfo?.label ?? fileContent.mimeType} · {formatFileSize(fileContent.size)}
        </span>
        <RevealButton />
      </div>
    );

    // Image preview
    if (viewType === 'image' && fileContent.encoding === 'base64') {
      return (
        <div className="flex flex-col items-center">
          <img
            src={`data:${fileContent.mimeType};base64,${fileContent.content}`}
            alt={file.name}
            className="max-w-full max-h-[55vh] object-contain rounded-lg border border-[var(--color-border)]"
          />
          {infoBar}
        </div>
      );
    }

    // PDF preview (lazy-loaded react-pdf renderer)
    if (viewType === 'pdf' && fileContent.encoding === 'base64') {
      return (
        <div className="flex flex-col">
          <Suspense fallback={
            <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)]">
              <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
              Loading PDF viewer...
            </div>
          }>
            <div className="max-h-[60vh] overflow-auto">
              <PdfRenderer
                filePath={file.path}
                fileName={file.name}
                content={fileContent.content}
                encoding="base64"
                mimeType="application/pdf"
                fileSize={fileContent.size}
              />
            </div>
          </Suspense>
          {infoBar}
        </div>
      );
    }

    // HTML preview — sandboxed iframe
    if (viewType === 'html-preview' && fileContent.encoding === 'utf-8') {
      return (
        <div className="flex flex-col">
          <iframe
            sandbox="allow-same-origin"
            srcDoc={fileContent.content}
            className="w-full h-[55vh] rounded-lg border border-[var(--color-border)] bg-white"
            title={file.name}
          />
          {infoBar}
        </div>
      );
    }

    // Text / Markdown / SVG / CSV / code — syntax-highlighted preview
    return (
      <div className="flex flex-col">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-[var(--color-text-muted)]">
            {typeInfo?.label ?? fileContent.mimeType} · {formatFileSize(fileContent.size)}
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
