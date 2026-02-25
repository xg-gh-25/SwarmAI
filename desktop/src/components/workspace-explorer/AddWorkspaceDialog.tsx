/**
 * AddWorkspaceDialog — Deprecated stub.
 *
 * This component previously allowed users to create new workspaces by pointing
 * to an existing folder or creating a new one. With the single-workspace model
 * (SwarmWS), workspace creation is no longer a user action. This stub is
 * retained for import compatibility and will be removed in a future cadence.
 *
 * Exports:
 * - ``AddWorkspaceDialog`` — No-op modal component (default export)
 */

import Modal from '../common/Modal';

interface AddWorkspaceDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onWorkspaceAdded?: () => void;
}

export default function AddWorkspaceDialog({
  isOpen,
  onClose,
}: AddWorkspaceDialogProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Add Workspace" size="md">
      <p className="text-sm text-[var(--color-text-muted)]">
        SwarmWS is the single workspace. Use Projects to organize your work.
      </p>
    </Modal>
  );
}
