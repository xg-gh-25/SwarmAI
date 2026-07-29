/**
 * BrainHubOverlay — Brain Hub hosted in the shared fullscreen Modal.
 *
 * Run 1 replaced the former static demo iframe (public/brain-hub-demo.html) with
 * the real React <BrainHub/> product surface. This component is the overlay SHELL:
 * it listens for the `swarm:show-brain-hub` window event (fired by the
 * nav-brain-hub button in ThreeColumnLayout) and renders <BrainHub/> inside the
 * shared common/<Modal size="fullscreen"/> — the SAME modal contract as OS Eval
 * (EvalModal) and Settings, so Brain Hub sits inset from the window edges
 * (w-90vw · max-w-6xl, dimmed backdrop, Esc / close / backdrop-click) and reads
 * as a floating layer rather than the main surface. Backdrop + Esc + body-scroll
 * lock are all owned by Modal; this shell only wires the open/close state to the
 * window event. The component export name is kept as `BrainHubDemoOverlay` to
 * avoid churning the ThreeColumnLayout import.
 */

import { useEffect, useState, useCallback } from 'react';
import Modal from '../common/Modal';
import { BrainHub } from './BrainHub';

export function BrainHubDemoOverlay() {
  const [open, setOpen] = useState(false);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener('swarm:show-brain-hub', show);
    return () => window.removeEventListener('swarm:show-brain-hub', show);
  }, []);

  return (
    <Modal
      isOpen={open}
      onClose={close}
      title="Brain Hub — phase-1 · read-only lens"
      size="fullscreen"
    >
      <div className="flex-1 overflow-hidden" data-testid="brain-hub-overlay">
        <BrainHub />
      </div>
    </Modal>
  );
}
