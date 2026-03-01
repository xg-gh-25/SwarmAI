/**
 * SkillsModal Component
 * 
 * Wraps the SkillsPage content in a full-screen modal overlay.
 * Opens from the Left Sidebar navigation when Skills icon is clicked.
 * 
 * Requirements: 2.2, 7.2
 */

import Modal from '../common/Modal';
import SkillsPage from '../../pages/SkillsPage';

interface SkillsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SkillsModal({ isOpen, onClose }: SkillsModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Skills"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <SkillsPage />
      </div>
    </Modal>
  );
}
