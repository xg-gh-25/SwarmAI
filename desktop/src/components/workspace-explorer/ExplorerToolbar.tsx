import { useState, useRef } from 'react';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { writeTextFile, mkdir } from '@tauri-apps/plugin-fs';
import { useLayout } from '../../contexts/LayoutContext';

/**
 * ExplorerToolbar component - toolbar with file operation buttons
 * 
 * Requirements:
 * - 3.7: Display toolbar with New File, New Folder, and Upload buttons
 * - 3.8: Create new file in current directory on New File click
 * - 3.9: Create new folder in current directory on New Folder click
 * - 3.10: Open file picker to upload files on Upload click
 */

interface ExplorerToolbarProps {
  /** Currently selected directory path (null if no directory selected) */
  selectedPath: string | null;
  /** The workspace ID for the selected path */
  selectedWorkspaceId: string | null;
  /** Whether the toolbar should be disabled (e.g., no workspace selected) */
  disabled?: boolean;
  /** Callback when a file or folder is created/uploaded */
  onFileSystemChange?: () => void;
}

interface DialogState {
  isOpen: boolean;
  type: 'file' | 'folder' | null;
  name: string;
  error: string | null;
}

export default function ExplorerToolbar({
  selectedPath,
  selectedWorkspaceId,
  disabled = false,
  onFileSystemChange,
}: ExplorerToolbarProps) {
  const { selectedWorkspaceScope } = useLayout();
  
  // Dialog state for new file/folder creation
  const [dialogState, setDialogState] = useState<DialogState>({
    isOpen: false,
    type: null,
    name: '',
    error: null,
  });
  
  const inputRef = useRef<HTMLInputElement>(null);
  
  // Determine if toolbar should be disabled
  // Disabled when "All Workspaces" is selected or no workspace context
  const isDisabled = disabled || selectedWorkspaceScope === 'all' || !selectedWorkspaceId;
  
  // Get the target directory for file operations
  const getTargetDirectory = (): string | null => {
    if (!selectedPath) return null;
    return selectedPath;
  };

  // Open dialog for new file
  const handleNewFile = () => {
    if (isDisabled) return;
    setDialogState({
      isOpen: true,
      type: 'file',
      name: '',
      error: null,
    });
    // Focus input after dialog opens
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // Open dialog for new folder
  const handleNewFolder = () => {
    if (isDisabled) return;
    setDialogState({
      isOpen: true,
      type: 'folder',
      name: '',
      error: null,
    });
    // Focus input after dialog opens
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // Handle upload button click
  const handleUpload = async () => {
    if (isDisabled) return;
    
    const targetDir = getTargetDirectory();
    if (!targetDir) {
      console.warn('No target directory selected for upload');
      return;
    }

    try {
      // Open file picker dialog
      const selected = await openDialog({
        multiple: true,
        directory: false,
        title: 'Select files to upload',
      });

      if (selected && selected.length > 0) {
        // Files selected - in a real implementation, we would copy them to the target directory
        // For now, we'll just trigger a refresh
        console.log('Files selected for upload:', selected);
        onFileSystemChange?.();
      }
    } catch (error) {
      console.error('Failed to open file picker:', error);
    }
  };

  // Handle dialog input change
  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setDialogState(prev => ({
      ...prev,
      name: e.target.value,
      error: null,
    }));
  };

  // Validate file/folder name
  const validateName = (name: string): string | null => {
    if (!name.trim()) {
      return 'Name cannot be empty';
    }
    // Check for invalid characters (including control characters)
    // eslint-disable-next-line no-control-regex
    const invalidChars = /[<>:"/\\|?*\x00-\x1f]/;
    if (invalidChars.test(name)) {
      return 'Name contains invalid characters';
    }
    // Check for reserved names (Windows)
    const reservedNames = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;
    if (reservedNames.test(name)) {
      return 'This name is reserved by the system';
    }
    return null;
  };

  // Handle dialog confirm
  const handleConfirm = async () => {
    const { type, name } = dialogState;
    
    // Validate name
    const validationError = validateName(name);
    if (validationError) {
      setDialogState(prev => ({ ...prev, error: validationError }));
      return;
    }

    const targetDir = getTargetDirectory();
    if (!targetDir) {
      setDialogState(prev => ({ ...prev, error: 'No target directory selected' }));
      return;
    }

    const fullPath = `${targetDir}/${name.trim()}`;

    try {
      if (type === 'file') {
        // Create empty file
        await writeTextFile(fullPath, '');
      } else if (type === 'folder') {
        // Create directory
        await mkdir(fullPath);
      }
      
      // Close dialog and trigger refresh
      setDialogState({ isOpen: false, type: null, name: '', error: null });
      onFileSystemChange?.();
    } catch (error) {
      console.error(`Failed to create ${type}:`, error);
      setDialogState(prev => ({
        ...prev,
        error: `Failed to create ${type}: ${error instanceof Error ? error.message : 'Unknown error'}`,
      }));
    }
  };

  // Handle dialog cancel
  const handleCancel = () => {
    setDialogState({ isOpen: false, type: null, name: '', error: null });
  };

  // Handle key press in dialog
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleConfirm();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      handleCancel();
    }
  };

  // Get tooltip text for disabled state
  const getDisabledTooltip = (): string => {
    if (selectedWorkspaceScope === 'all') {
      return 'Select a specific workspace to enable file operations';
    }
    if (!selectedWorkspaceId) {
      return 'Select a workspace to enable file operations';
    }
    return '';
  };

  return (
    <>
      <div 
        className="flex items-center gap-1 px-3 py-1.5 border-b border-[var(--color-border)]"
        data-testid="explorer-toolbar"
      >
        {/* New File Button */}
        <ToolbarButton
          icon="note_add"
          label="New File"
          onClick={handleNewFile}
          disabled={isDisabled}
          tooltip={isDisabled ? getDisabledTooltip() : 'Create new file'}
          testId="new-file-button"
        />

        {/* New Folder Button */}
        <ToolbarButton
          icon="create_new_folder"
          label="New Folder"
          onClick={handleNewFolder}
          disabled={isDisabled}
          tooltip={isDisabled ? getDisabledTooltip() : 'Create new folder'}
          testId="new-folder-button"
        />

        {/* Upload Button */}
        <ToolbarButton
          icon="upload_file"
          label="Upload"
          onClick={handleUpload}
          disabled={isDisabled}
          tooltip={isDisabled ? getDisabledTooltip() : 'Upload files'}
          testId="upload-button"
        />
      </div>

      {/* Creation Dialog */}
      {dialogState.isOpen && (
        <div 
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={handleCancel}
          data-testid="creation-dialog-overlay"
        >
          <div 
            className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg shadow-lg p-4 w-80"
            onClick={e => e.stopPropagation()}
            data-testid="creation-dialog"
          >
            <h3 className="text-sm font-medium text-[var(--color-text)] mb-3">
              {dialogState.type === 'file' ? 'Create New File' : 'Create New Folder'}
            </h3>
            
            <input
              ref={inputRef}
              type="text"
              value={dialogState.name}
              onChange={handleNameChange}
              onKeyDown={handleKeyDown}
              placeholder={dialogState.type === 'file' ? 'filename.txt' : 'folder-name'}
              className="w-full px-3 py-2 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]"
              data-testid="creation-dialog-input"
              aria-label={dialogState.type === 'file' ? 'File name' : 'Folder name'}
            />
            
            {dialogState.error && (
              <p 
                className="mt-2 text-xs text-[var(--color-error)]"
                data-testid="creation-dialog-error"
              >
                {dialogState.error}
              </p>
            )}
            
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={handleCancel}
                className="px-3 py-1.5 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
                data-testid="creation-dialog-cancel"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirm}
                className="px-3 py-1.5 text-sm rounded bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
                data-testid="creation-dialog-confirm"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * ToolbarButton component - individual toolbar button
 */
interface ToolbarButtonProps {
  icon: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tooltip?: string;
  testId?: string;
}

function ToolbarButton({ 
  icon, 
  label, 
  onClick, 
  disabled = false, 
  tooltip,
  testId,
}: ToolbarButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={tooltip || label}
      className={`p-1.5 rounded transition-colors ${
        disabled
          ? 'text-[var(--color-text-muted)] opacity-50 cursor-not-allowed'
          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      }`}
      aria-label={label}
      data-testid={testId}
    >
      <span className="material-symbols-outlined text-lg">{icon}</span>
    </button>
  );
}

// Export sub-components for testing
export { ToolbarButton };
