import { useState, useRef, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import clsx from 'clsx';
import type { SwarmWorkspace } from '../../types';
import { swarmWorkspacesService } from '../../services/swarmWorkspaces';

interface WorkspaceSelectorProps {
  selectedWorkspaceId: string | null;
  onSelect: (workspace: SwarmWorkspace) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * WorkspaceSelector - Dropdown component for selecting a Swarm Workspace in chat.
 * Displays all workspaces with name and icon, highlights the currently selected one.
 * Replaces the folder picker in the chat interface.
 * 
 * Requirements: 5.1, 5.2
 */
export function WorkspaceSelector({
  selectedWorkspaceId,
  onSelect,
  disabled = false,
  className,
}: WorkspaceSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Fetch all workspaces
  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: ['swarmWorkspaces'],
    queryFn: swarmWorkspacesService.list,
  });

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleToggle = () => {
    if (disabled || isLoading) return;
    setIsOpen(!isOpen);
  };

  const handleSelect = (workspace: SwarmWorkspace) => {
    onSelect(workspace);
    setIsOpen(false);
  };

  const selectedWorkspace = workspaces.find((ws) => ws.id === selectedWorkspaceId);

  return (
    <div className={clsx('relative', className)} ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        onClick={handleToggle}
        disabled={disabled || isLoading}
        className={clsx(
          'p-2 rounded-lg transition-colors flex items-center gap-1',
          selectedWorkspace
            ? 'text-primary bg-primary/10 hover:bg-primary/20'
            : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]',
          (disabled || isLoading) && 'opacity-50 cursor-not-allowed'
        )}
        title={selectedWorkspace ? `Workspace: ${selectedWorkspace.name}` : 'Select a workspace'}
      >
        {isLoading ? (
          <span className="material-symbols-outlined animate-spin">progress_activity</span>
        ) : selectedWorkspace?.icon ? (
          <span className="text-lg">{selectedWorkspace.icon}</span>
        ) : (
          <span className="material-symbols-outlined">workspaces</span>
        )}
        <span
          className={clsx(
            'material-symbols-outlined text-sm transition-transform',
            isOpen && 'rotate-180'
          )}
        >
          expand_more
        </span>
      </button>

      {/* Dropdown Menu */}
      {isOpen && !disabled && (
        <div className="absolute bottom-full left-0 mb-2 w-64 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg shadow-xl overflow-hidden z-50">
          <div className="px-3 py-2 border-b border-[var(--color-border)]">
            <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
              Workspaces
            </span>
          </div>

          <div className="max-h-60 overflow-y-auto">
            {workspaces.length === 0 ? (
              <div className="px-3 py-4 text-center text-[var(--color-text-muted)] text-sm">
                No workspaces available
              </div>
            ) : (
              workspaces.map((workspace) => {
                const isSelected = selectedWorkspaceId === workspace.id;
                return (
                  <button
                    key={workspace.id}
                    onClick={() => handleSelect(workspace)}
                    className={clsx(
                      'w-full px-3 py-2.5 flex items-start gap-3 text-left transition-colors',
                      isSelected
                        ? 'bg-primary text-white'
                        : 'text-[var(--color-text)] hover:bg-[var(--color-hover)]'
                    )}
                  >
                    {/* Workspace Icon */}
                    <span className="text-lg flex-shrink-0">
                      {workspace.icon || '📁'}
                    </span>

                    {/* Workspace Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="font-medium truncate">{workspace.name}</p>
                        {workspace.isDefault && (
                          <span
                            className={clsx(
                              'text-xs px-1.5 py-0.5 rounded',
                              isSelected
                                ? 'bg-white/20 text-white'
                                : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]'
                            )}
                          >
                            Default
                          </span>
                        )}
                      </div>
                      <p
                        className={clsx(
                          'text-xs truncate',
                          isSelected ? 'text-white/70' : 'text-[var(--color-text-muted)]'
                        )}
                      >
                        {workspace.filePath}
                      </p>
                    </div>

                    {/* Selected Indicator */}
                    {isSelected && (
                      <span className="material-symbols-outlined text-white flex-shrink-0">
                        check
                      </span>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default WorkspaceSelector;
