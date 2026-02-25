/**
 * WorkspaceSelector — Singleton workspace indicator for the chat interface.
 *
 * Simplified from a multi-workspace dropdown to a static display showing
 * the single SwarmWS workspace. The new workspace service (task 13.2) will
 * provide the actual config; for now this uses a hardcoded "SwarmWS" label.
 *
 * Exports:
 * - ``WorkspaceSelector`` — React component (named + default export)
 */

import clsx from 'clsx';

interface WorkspaceSelectorProps {
  disabled?: boolean;
  className?: string;
}

/**
 * WorkspaceSelector - Static indicator for the singleton SwarmWS workspace.
 * No dropdown, no multi-workspace selection.
 */
export function WorkspaceSelector({
  disabled = false,
  className,
}: WorkspaceSelectorProps) {
  return (
    <div className={clsx('relative', className)}>
      <button
        disabled={disabled}
        className={clsx(
          'p-2 rounded-lg transition-colors flex items-center gap-1',
          'text-primary bg-primary/10',
          disabled && 'opacity-50 cursor-not-allowed'
        )}
        title="Workspace: SwarmWS"
      >
        <span className="text-lg">🏠</span>
        <span className="text-sm font-medium text-[var(--color-text)]">SwarmWS</span>
      </button>
    </div>
  );
}

export default WorkspaceSelector;
