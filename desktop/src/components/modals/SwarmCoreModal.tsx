/**
 * SwarmCoreModal Component
 * 
 * Wraps the SwarmCorePage content in a full-screen modal overlay.
 * Opens from the Left Sidebar navigation when SwarmCore icon is clicked.
 * Displays dashboard with system statistics and quick actions.
 * 
 * Requirements: 5.1, 5.2, 5.3, 5.4
 */

import Modal from '../common/Modal';
import SwarmCorePage from '../../pages/SwarmCorePage';

interface SwarmCoreModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SwarmCoreModal({ isOpen, onClose }: SwarmCoreModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="SwarmCore"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <SwarmCorePage />
      </div>
    </Modal>
  );
}
