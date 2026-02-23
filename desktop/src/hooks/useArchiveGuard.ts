import { useCallback } from 'react';

export interface UseArchiveGuardReturn {
  /** Whether the workspace is archived (read-only) */
  isReadOnly: boolean;
  /** Show a toast/alert when user attempts a write operation on archived workspace */
  guardWrite: (action?: string) => boolean;
}

/**
 * Hook that provides read-only enforcement for archived workspaces.
 *
 * Usage:
 *   const { isReadOnly, guardWrite } = useArchiveGuard(selectedWorkspace?.isArchived);
 *   // In a handler:
 *   if (!guardWrite('create a signal')) return;
 *
 * Requirements: 36.6
 */
export function useArchiveGuard(isArchived: boolean = false): UseArchiveGuardReturn {
  const guardWrite = useCallback(
    (action?: string): boolean => {
      if (isArchived) {
        const msg = action
          ? `Cannot ${action} — this workspace is archived (read-only). Unarchive it first to make changes.`
          : 'This workspace is archived and read-only. Unarchive it first to make changes.';
        // Use window.alert as a simple notification; can be replaced with toast later
        window.alert(msg);
        return false;
      }
      return true;
    },
    [isArchived]
  );

  return {
    isReadOnly: isArchived,
    guardWrite,
  };
}
