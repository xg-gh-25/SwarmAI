/**
 * BrainHubOverlay — full-screen overlay host for the real DDD Brain Hub.
 *
 * Run 1 replaced the former static demo iframe (public/brain-hub-demo.html) with
 * the real React <BrainHub/> product surface. This component is now just the
 * overlay SHELL: it listens for the `swarm:show-brain-hub` window event (fired by
 * the nav-brain-hub button in ThreeColumnLayout), renders a `fixed inset-0 z-50`
 * overlay, closes on Esc / the close button, and mounts <BrainHub/> as its body.
 *
 * The component export name is kept as `BrainHubDemoOverlay` to avoid churning
 * the ThreeColumnLayout import; the data-testid is `brain-hub-overlay`.
 */

import { useEffect, useState, useCallback } from 'react';
import { BrainHub } from './BrainHub';

export function BrainHubDemoOverlay() {
  const [open, setOpen] = useState(false);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener('swarm:show-brain-hub', show);
    return () => window.removeEventListener('swarm:show-brain-hub', show);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, close]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-[#0e1117] flex flex-col" data-testid="brain-hub-overlay">
      <div className="flex items-center gap-2 px-4 h-10 border-b border-[#222831] flex-shrink-0">
        <span className="material-symbols-outlined text-[18px] text-[#f0a500]">psychology</span>
        <span className="text-[13px] font-semibold text-[#e6edf3]">Brain Hub</span>
        <span className="text-[10px] font-mono text-[#5b636d]">phase-1 · read-only lens</span>
        <button
          onClick={close}
          data-testid="brain-hub-close"
          className="ml-auto flex items-center gap-1 text-[12px] text-[#8b949e] hover:text-[#e6edf3] px-2 py-1 rounded-md hover:bg-[#1f2630]"
        >
          <span className="material-symbols-outlined text-[16px]">close</span>
          Close
        </button>
      </div>
      <div className="flex-1 overflow-hidden">
        <BrainHub />
      </div>
    </div>
  );
}
