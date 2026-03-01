/**
 * AgentsModal Component
 * 
 * Wraps the AgentsPage content in a full-screen modal overlay.
 * Opens from the Left Sidebar navigation when Agents icon is clicked.
 * Displays list of Custom_Agents with CRUD operations.
 * 
 * Requirements: 2.2, 7.1, 8.1, 8.2
 */

import Modal from '../common/Modal';
import AgentsPage from '../../pages/AgentsPage';

interface AgentsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function AgentsModal({ isOpen, onClose }: AgentsModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Agents"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <AgentsPage />
      </div>
    </Modal>
  );
}
