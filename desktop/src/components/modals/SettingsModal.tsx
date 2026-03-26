/**
 * SettingsModal Component
 *
 * Wraps the SettingsPage content in a modal overlay for the three-column layout.
 * Opens from the Left Sidebar navigation. Supports initialTab for deep-linking
 * (e.g., clicking Skills icon opens Settings with Skills tab active).
 */

import Modal from '../common/Modal';
import SettingsPage from '../../pages/SettingsPage';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: string;
}

export default function SettingsModal({ isOpen, onClose, initialTab }: SettingsModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Settings"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <SettingsPage initialTab={initialTab} />
      </div>
    </Modal>
  );
}
