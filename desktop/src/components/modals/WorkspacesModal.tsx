/**
 * WorkspacesModal Component (Stub)
 *
 * Placeholder modal retained for layout compatibility during the
 * SwarmWS single-workspace migration. The multi-workspace WorkspacesPage
 * has been removed. This component will be fully replaced or removed
 * in a future cadence once the Workspace Explorer UX redesign is complete.
 */

import Modal from '../common/Modal';

interface WorkspacesModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function WorkspacesModal({ isOpen, onClose }: WorkspacesModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="SwarmWS"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6 p-6">
        <p className="text-[var(--color-text-muted)]">
          SwarmWS is your single persistent workspace. Manage projects and settings from the Workspace Explorer.
        </p>
      </div>
    </Modal>
  );
}
