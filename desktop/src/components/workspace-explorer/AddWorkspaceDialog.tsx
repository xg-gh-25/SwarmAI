import { useState, useRef, useEffect } from 'react';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { mkdir, exists } from '@tauri-apps/plugin-fs';
import Modal from '../common/Modal';
import { swarmWorkspacesService } from '../../services/swarmWorkspaces';

/**
 * AddWorkspaceDialog component - dialog for adding new workspaces
 * 
 * Requirements:
 * - 5.1: Provide "Add Workspace" button in Workspace Explorer
 * - 5.2: Support "Point to existing folder" option
 * - 5.3: Support "Create new folder" option
 * - 5.4: Validate workspace path before adding
 */

interface AddWorkspaceDialogProps {
  /** Whether the dialog is open */
  isOpen: boolean;
  /** Callback when dialog is closed */
  onClose: () => void;
  /** Callback when workspace is successfully added */
  onWorkspaceAdded?: () => void;
}

type DialogMode = 'select' | 'existing' | 'new';

interface FormState {
  name: string;
  path: string;
  parentPath: string;
  error: string | null;
  isSubmitting: boolean;
}

const initialFormState: FormState = {
  name: '',
  path: '',
  parentPath: '',
  error: null,
  isSubmitting: false,
};

export default function AddWorkspaceDialog({
  isOpen,
  onClose,
  onWorkspaceAdded,
}: AddWorkspaceDialogProps) {
  const [mode, setMode] = useState<DialogMode>('select');
  const [formState, setFormState] = useState<FormState>(initialFormState);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Reset state when dialog opens/closes
  useEffect(() => {
    if (isOpen) {
      setMode('select');
      setFormState(initialFormState);
    }
  }, [isOpen]);

  // Focus name input when entering 'new' mode
  useEffect(() => {
    if (mode === 'new' && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [mode]);

  // Validate workspace name
  const validateName = (name: string): string | null => {
    if (!name.trim()) {
      return 'Workspace name cannot be empty';
    }
    // Check for invalid characters
    const invalidChars = /[<>:"/\\|?*]/;
    if (invalidChars.test(name)) {
      return 'Name contains invalid characters';
    }
    if (name.length > 255) {
      return 'Name is too long (max 255 characters)';
    }
    return null;
  };

  // Validate workspace path - Requirement 5.4
  const validatePath = async (path: string): Promise<string | null> => {
    if (!path.trim()) {
      return 'Path cannot be empty';
    }
    
    try {
      // Check if path exists and is accessible
      const pathExists = await exists(path);
      if (!pathExists) {
        return 'The selected path does not exist or is not accessible';
      }
      return null;
    } catch {
      return 'Unable to validate path. Please check if the path is accessible.';
    }
  };

  // Handle "Point to existing folder" - Requirement 5.2
  const handleBrowseExisting = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: 'Select Workspace Folder',
      });

      if (selected && typeof selected === 'string') {
        // Extract folder name from path for workspace name
        const folderName = selected.split('/').pop() || selected.split('\\').pop() || 'Workspace';
        
        setFormState(prev => ({
          ...prev,
          path: selected,
          name: folderName,
          error: null,
        }));
        setMode('existing');
      }
    } catch (error) {
      console.error('Failed to open directory picker:', error);
      setFormState(prev => ({
        ...prev,
        error: 'Failed to open directory picker',
      }));
    }
  };

  // Handle "Create new folder" parent selection - Requirement 5.3
  const handleBrowseParent = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: 'Select Parent Folder for New Workspace',
      });

      if (selected && typeof selected === 'string') {
        setFormState(prev => ({
          ...prev,
          parentPath: selected,
          error: null,
        }));
      }
    } catch (error) {
      console.error('Failed to open directory picker:', error);
      setFormState(prev => ({
        ...prev,
        error: 'Failed to open directory picker',
      }));
    }
  };

  // Handle form submission for existing folder
  const handleSubmitExisting = async () => {
    const { name, path } = formState;

    // Validate name
    const nameError = validateName(name);
    if (nameError) {
      setFormState(prev => ({ ...prev, error: nameError }));
      return;
    }

    // Validate path - Requirement 5.4
    const pathError = await validatePath(path);
    if (pathError) {
      setFormState(prev => ({ ...prev, error: pathError }));
      return;
    }

    setFormState(prev => ({ ...prev, isSubmitting: true, error: null }));

    try {
      await swarmWorkspacesService.create({
        name: name.trim(),
        filePath: path,
        context: '',
      });

      onWorkspaceAdded?.();
      onClose();
    } catch (error) {
      console.error('Failed to create workspace:', error);
      setFormState(prev => ({
        ...prev,
        isSubmitting: false,
        error: error instanceof Error ? error.message : 'Failed to create workspace',
      }));
    }
  };

  // Handle form submission for new folder - Requirement 5.3
  const handleSubmitNew = async () => {
    const { name, parentPath } = formState;

    // Validate name
    const nameError = validateName(name);
    if (nameError) {
      setFormState(prev => ({ ...prev, error: nameError }));
      return;
    }

    // Validate parent path
    if (!parentPath) {
      setFormState(prev => ({ ...prev, error: 'Please select a parent folder' }));
      return;
    }

    const pathError = await validatePath(parentPath);
    if (pathError) {
      setFormState(prev => ({ ...prev, error: pathError }));
      return;
    }

    setFormState(prev => ({ ...prev, isSubmitting: true, error: null }));

    const newFolderPath = `${parentPath}/${name.trim()}`;

    try {
      // Check if folder already exists
      const folderExists = await exists(newFolderPath);
      if (folderExists) {
        setFormState(prev => ({
          ...prev,
          isSubmitting: false,
          error: 'A folder with this name already exists at the selected location',
        }));
        return;
      }

      // Create the new folder
      await mkdir(newFolderPath);

      // Create the workspace
      await swarmWorkspacesService.create({
        name: name.trim(),
        filePath: newFolderPath,
        context: '',
      });

      onWorkspaceAdded?.();
      onClose();
    } catch (error) {
      console.error('Failed to create workspace:', error);
      setFormState(prev => ({
        ...prev,
        isSubmitting: false,
        error: error instanceof Error ? error.message : 'Failed to create workspace',
      }));
    }
  };

  // Handle name input change
  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormState(prev => ({
      ...prev,
      name: e.target.value,
      error: null,
    }));
  };

  // Handle key press
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !formState.isSubmitting) {
      e.preventDefault();
      if (mode === 'existing') {
        handleSubmitExisting();
      } else if (mode === 'new') {
        handleSubmitNew();
      }
    }
  };

  // Render mode selection view
  const renderModeSelection = () => (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Choose how you want to add a workspace:
      </p>

      {/* Point to existing folder - Requirement 5.2 */}
      <button
        onClick={handleBrowseExisting}
        className="w-full p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-hover)] hover:border-[var(--color-primary)] transition-colors text-left group"
        data-testid="existing-folder-option"
      >
        <div className="flex items-center gap-3">
          <span className="material-symbols-outlined text-2xl text-[var(--color-primary)]">
            folder_open
          </span>
          <div>
            <div className="font-medium text-[var(--color-text)]">
              Point to existing folder
            </div>
            <div className="text-sm text-[var(--color-text-muted)]">
              Select an existing folder on your computer
            </div>
          </div>
        </div>
      </button>

      {/* Create new folder - Requirement 5.3 */}
      <button
        onClick={() => setMode('new')}
        className="w-full p-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-hover)] hover:border-[var(--color-primary)] transition-colors text-left group"
        data-testid="new-folder-option"
      >
        <div className="flex items-center gap-3">
          <span className="material-symbols-outlined text-2xl text-[var(--color-primary)]">
            create_new_folder
          </span>
          <div>
            <div className="font-medium text-[var(--color-text)]">
              Create new folder
            </div>
            <div className="text-sm text-[var(--color-text-muted)]">
              Create a new empty folder for your workspace
            </div>
          </div>
        </div>
      </button>
    </div>
  );

  // Render existing folder form
  const renderExistingForm = () => (
    <div className="space-y-4">
      {/* Back button */}
      <button
        onClick={() => setMode('select')}
        className="flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        data-testid="back-button"
      >
        <span className="material-symbols-outlined text-sm">arrow_back</span>
        Back
      </button>

      {/* Selected path display */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-1">
          Selected Folder
        </label>
        <div className="flex items-center gap-2">
          <div className="flex-1 px-3 py-2 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text-muted)] truncate">
            {formState.path || 'No folder selected'}
          </div>
          <button
            onClick={handleBrowseExisting}
            className="px-3 py-2 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            data-testid="change-folder-button"
          >
            Change
          </button>
        </div>
      </div>

      {/* Workspace name input */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-1">
          Workspace Name
        </label>
        <input
          ref={nameInputRef}
          type="text"
          value={formState.name}
          onChange={handleNameChange}
          onKeyDown={handleKeyDown}
          placeholder="My Workspace"
          className="w-full px-3 py-2 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]"
          data-testid="workspace-name-input"
          aria-label="Workspace name"
        />
      </div>

      {/* Error message */}
      {formState.error && (
        <p className="text-sm text-[var(--color-error)]" data-testid="error-message">
          {formState.error}
        </p>
      )}

      {/* Action buttons */}
      <div className="flex justify-end gap-2 pt-2">
        <button
          onClick={onClose}
          className="px-4 py-2 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          disabled={formState.isSubmitting}
          data-testid="cancel-button"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmitExisting}
          disabled={formState.isSubmitting || !formState.path}
          className="px-4 py-2 text-sm rounded bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="add-workspace-button"
        >
          {formState.isSubmitting ? 'Adding...' : 'Add Workspace'}
        </button>
      </div>
    </div>
  );

  // Render new folder form - Requirement 5.3
  const renderNewFolderForm = () => (
    <div className="space-y-4">
      {/* Back button */}
      <button
        onClick={() => setMode('select')}
        className="flex items-center gap-1 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        data-testid="back-button"
      >
        <span className="material-symbols-outlined text-sm">arrow_back</span>
        Back
      </button>

      {/* Workspace name input */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-1">
          Workspace Name
        </label>
        <input
          ref={nameInputRef}
          type="text"
          value={formState.name}
          onChange={handleNameChange}
          onKeyDown={handleKeyDown}
          placeholder="my-new-project"
          className="w-full px-3 py-2 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)] focus:border-[var(--color-primary)]"
          data-testid="workspace-name-input"
          aria-label="Workspace name"
        />
      </div>

      {/* Parent folder selection */}
      <div>
        <label className="block text-sm font-medium text-[var(--color-text)] mb-1">
          Location
        </label>
        <div className="flex items-center gap-2">
          <div className="flex-1 px-3 py-2 text-sm rounded border border-[var(--color-border)] bg-[var(--color-bg)] text-[var(--color-text-muted)] truncate">
            {formState.parentPath || 'No location selected'}
          </div>
          <button
            onClick={handleBrowseParent}
            className="px-3 py-2 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            data-testid="browse-location-button"
          >
            Browse
          </button>
        </div>
        {formState.parentPath && formState.name && (
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Will create: {formState.parentPath}/{formState.name}
          </p>
        )}
      </div>

      {/* Error message */}
      {formState.error && (
        <p className="text-sm text-[var(--color-error)]" data-testid="error-message">
          {formState.error}
        </p>
      )}

      {/* Action buttons */}
      <div className="flex justify-end gap-2 pt-2">
        <button
          onClick={onClose}
          className="px-4 py-2 text-sm rounded border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          disabled={formState.isSubmitting}
          data-testid="cancel-button"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmitNew}
          disabled={formState.isSubmitting || !formState.name || !formState.parentPath}
          className="px-4 py-2 text-sm rounded bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="create-workspace-button"
        >
          {formState.isSubmitting ? 'Creating...' : 'Create Workspace'}
        </button>
      </div>
    </div>
  );

  // Render content based on mode
  const renderContent = () => {
    switch (mode) {
      case 'select':
        return renderModeSelection();
      case 'existing':
        return renderExistingForm();
      case 'new':
        return renderNewFolderForm();
      default:
        return null;
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Add Workspace"
      size="md"
    >
      {renderContent()}
    </Modal>
  );
}
