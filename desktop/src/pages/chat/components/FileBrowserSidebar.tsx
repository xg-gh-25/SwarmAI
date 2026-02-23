import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { FileBrowser } from '../../../components/workspace/FileBrowser';

interface FileBrowserSidebarProps {
  width: number;
  isResizing: boolean;
  selectedAgentId: string | null;
  basePath?: string;
  onFileSelect: (file: { path: string; name: string } | null) => void;
  /** Optional close handler. If not provided, close button is hidden. */
  onClose?: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
}

/**
 * Right Sidebar - File Browser Component
 */
export function FileBrowserSidebar({
  width,
  isResizing,
  selectedAgentId,
  basePath,
  onFileSelect,
  onClose,
  onMouseDown,
}: FileBrowserSidebarProps) {
  const { t } = useTranslation();

  return (
    <div
      className="flex flex-col bg-[var(--color-card)] border-l border-[var(--color-border)] relative"
      style={{ width }}
    >
      {/* Resize Handle (on the left side) */}
      <div
        className={clsx(
          'absolute top-0 left-0 w-1 h-full cursor-ew-resize hover:bg-primary/50 transition-colors z-10',
          isResizing && 'bg-primary'
        )}
        onMouseDown={onMouseDown}
      >
        <div className="absolute inset-y-0 -left-1 w-3" />
      </div>

      {/* Header */}
      <div className="h-12 px-4 flex items-center justify-between border-b border-[var(--color-border)] flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-lg">folder</span>
          <span className="font-medium text-[var(--color-text)] text-sm">Files</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            aria-label="Close file browser"
          >
            <span className="material-symbols-outlined text-lg">close</span>
          </button>
        )}
      </div>

      {/* File Browser Content */}
      <div className="flex-1 overflow-hidden">
        {selectedAgentId ? (
          <FileBrowser
            agentId={selectedAgentId}
            onFileSelect={onFileSelect}
            className="h-full"
            basePath={basePath}
          />
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)] p-4 text-center">
            <span className="material-symbols-outlined text-3xl mb-2">folder_off</span>
            <p className="text-sm">{t('chat.noAgent')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
