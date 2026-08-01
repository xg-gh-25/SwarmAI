/**
 * SwarmWSOverlay — the workspace file explorer hosted in the shared fullscreen Modal.
 *
 * Part of the A10 left-nav redesign (run_1aab916c): the WorkspaceExplorer is no
 * longer an always-on sibling column — it opens on demand as a fullscreen overlay
 * when the SwarmWS domain card (or the logo button) fires `swarm:show-swarmws`.
 * Same overlay contract as BrainHubDemoOverlay / EvalModal / Settings: common
 * <Modal size="fullscreen"/> owns backdrop + Esc + body-scroll-lock; this shell
 * only wires open/close to the window event and renders <WorkspaceExplorer/>
 * (which now fills its parent — the former column/collapse mode was deleted).
 *
 * Gate-1 fix (z-index): FileViewerPanel renders as a `relative` sibling in the
 * main flex row (no z-index) while Modal is `fixed z-50`. If a file were opened
 * from inside this overlay it would render UNDER the modal. So opening a file
 * (double-click → onFileDoubleClick) CLOSES the overlay first, then delegates to
 * the parent's handler — the file lands in the now-unobscured FileViewer panel.
 */

import { useCallback } from 'react';
import Modal from '../common/Modal';
import { WorkspaceExplorer } from '../workspace-explorer';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { useExclusiveOverlay } from './useExclusiveOverlay';

export interface SwarmWSOverlayProps {
  /** Parent handler (ThreeColumnLayout) that opens a file in the FileViewer panel. */
  onFileDoubleClick?: (file: FileTreeItem, autoDiff?: boolean) => void;
}

export function SwarmWSOverlay({ onFileDoubleClick }: SwarmWSOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-swarmws');

  // Gate-1 z-index fix: close the overlay BEFORE opening the file, so the
  // FileViewer panel (relative, no z-index) is not rendered under this z-50 Modal.
  const handleFileDoubleClick = useCallback(
    (file: FileTreeItem) => {
      close();
      onFileDoubleClick?.(file);
    },
    [onFileDoubleClick, close],
  );

  return (
    <Modal isOpen={open} onClose={close} title="SwarmWS — workspace explorer" size="fullscreen">
      <div className="flex-1 overflow-hidden" data-testid="swarmws-overlay">
        <WorkspaceExplorer onFileDoubleClick={handleFileDoubleClick} />
      </div>
    </Modal>
  );
}
