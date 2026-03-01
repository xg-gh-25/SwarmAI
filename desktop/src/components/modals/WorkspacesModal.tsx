/**
 * WorkspacesModal Component
 * 
 * Wraps the WorkspacesPage content in a full-screen modal overlay.
 * Opens from the Left Sidebar navigation when Workspaces icon is clicked.
 * Displays workspace management with CRUD operations.
 * 
 * Requirements: 4.1, 4.2, 4.3, 4.4
 */

import Modal from '../common/Modal';
import WorkspacesPage from '../../pages/WorkspacesPage';

interface WorkspacesModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function WorkspacesModal({ isOpen, onClose }: WorkspacesModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Workspaces"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <WorkspacesPage />
      </div>
    </Modal>
  );
}
