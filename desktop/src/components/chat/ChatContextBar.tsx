import clsx from 'clsx';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';

/**
 * ChatContextBar Props
 * 
 * From design.md:
 * - workspaceScope: 'all' | string (workspace ID)
 * - attachedFiles: FileTreeItem[] (files attached to chat context)
 * - onRemoveFile: callback to remove a file from context
 */
interface ChatContextBarProps {
  /** Current workspace scope - 'all' for all workspaces or specific workspace name */
  workspaceScope: string;
  /** List of files attached to the chat context */
  attachedFiles: FileTreeItem[];
  /** Callback when a file is removed from context */
  onRemoveFile: (file: FileTreeItem) => void;
}

/**
 * ChatContextBar - Displays workspace scope badge and attached files
 * 
 * Requirements:
 * - 6.3: Display current Workspace_Scope as a badge/indicator in chat area
 * - 6.4: Display attached files as removable chips/badges
 * - 6.7: Show visual indicator when files are attached to context
 */
export function ChatContextBar({ workspaceScope, attachedFiles, onRemoveFile }: ChatContextBarProps) {
  const hasAttachments = attachedFiles.length > 0;
  const isAllWorkspaces = workspaceScope === 'all';
  const displayScope = isAllWorkspaces ? 'All Workspaces' : workspaceScope;

  return (
    <div
      className={clsx(
        'flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]',
        'bg-[var(--color-card)]/50'
      )}
    >
      {/* Workspace Scope Badge */}
      <div
        className={clsx(
          'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium',
          'bg-[var(--color-hover)] text-[var(--color-text)]',
          'border border-[var(--color-border)]'
        )}
        title={`Workspace scope: ${displayScope}`}
      >
        <span className="material-symbols-outlined text-sm text-[var(--color-text-muted)]">folder_open</span>
        <span className="truncate max-w-[150px]">{displayScope}</span>
      </div>

      {/* Separator when there are attachments */}
      {hasAttachments && (
        <div className="h-4 w-px bg-[var(--color-border)]" />
      )}

      {/* Attached Files List */}
      {hasAttachments && (
        <div className="flex items-center gap-1.5 flex-1 overflow-x-auto">
          {/* Attachment indicator icon */}
          <span className="material-symbols-outlined text-sm text-primary flex-shrink-0">attach_file</span>
          
          {/* File chips */}
          <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-thin scrollbar-thumb-[var(--color-border)] scrollbar-track-transparent">
            {attachedFiles.map((file) => (
              <FileChip
                key={file.id}
                file={file}
                onRemove={() => onRemoveFile(file)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * FileChip - Individual removable file badge
 */
interface FileChipProps {
  file: FileTreeItem;
  onRemove: () => void;
}

function FileChip({ file, onRemove }: FileChipProps) {
  return (
    <div
      className={clsx(
        'group flex items-center gap-1 px-2 py-0.5 rounded-full text-xs',
        'bg-primary/10 text-primary border border-primary/20',
        'hover:bg-primary/20 transition-colors'
      )}
      title={file.path}
    >
      <span className="material-symbols-outlined text-xs flex-shrink-0">description</span>
      <span className="truncate max-w-[100px]">{file.name}</span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        className={clsx(
          'flex items-center justify-center w-4 h-4 rounded-full',
          'hover:bg-primary/30 transition-colors',
          'opacity-60 group-hover:opacity-100'
        )}
        title={`Remove ${file.name}`}
        aria-label={`Remove ${file.name} from context`}
      >
        <span className="material-symbols-outlined text-xs">close</span>
      </button>
    </div>
  );
}

export default ChatContextBar;
