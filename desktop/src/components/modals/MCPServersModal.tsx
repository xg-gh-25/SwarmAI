/**
 * MCPServersModal Component
 * 
 * Wraps the MCPPage content in a full-screen modal overlay.
 * Opens from the Left Sidebar navigation when MCP Servers icon is clicked.
 * 
 * Requirements: 2.2, 7.3
 */

import Modal from '../common/Modal';
import MCPPage from '../../pages/MCPPage';

interface MCPServersModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function MCPServersModal({ isOpen, onClose }: MCPServersModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="MCP Servers"
      size="fullscreen"
    >
      <div className="h-full overflow-y-auto -m-6">
        <MCPPageContent />
      </div>
    </Modal>
  );
}

/**
 * MCPPageContent - Renders the MCP page content without the breadcrumb
 * and with adjusted padding for modal context
 */
function MCPPageContent() {
  return (
    <div className="mcp-modal-content">
      <MCPPage />
    </div>
  );
}
