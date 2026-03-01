/**
 * SettingsModal Component
 * 
 * Wraps the SettingsPage content in a modal overlay for the three-column layout.
 * Opens from the Left Sidebar navigation.
 * 
 * Requirements: 2.2, 7.4
 */

import Modal from '../common/Modal';
import SettingsPage from '../../pages/SettingsPage';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Settings"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <SettingsPageContent />
      </div>
    </Modal>
  );
}

/**
 * SettingsPageContent - Renders the settings page content without the breadcrumb
 * and with adjusted padding for modal context
 */
function SettingsPageContent() {
  return (
    <div className="settings-modal-content">
      <SettingsPage />
    </div>
  );
}
