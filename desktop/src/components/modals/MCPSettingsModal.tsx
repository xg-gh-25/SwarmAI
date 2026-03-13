/**
 * MCPSettingsModal — Wraps MCPSettingsPanel in a fullscreen modal.
 *
 * Replaces the old MCPServersModal that wrapped MCPPage.
 */

import Modal from '../common/Modal';
import MCPSettingsPanel from '../workspace-settings/MCPSettingsPanel';

interface MCPSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function MCPSettingsModal({ isOpen, onClose }: MCPSettingsModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title="MCP Servers" size="fullscreen">
      <div className="h-full overflow-y-auto -m-6 p-8">
        <MCPSettingsPanel />
      </div>
    </Modal>
  );
}
