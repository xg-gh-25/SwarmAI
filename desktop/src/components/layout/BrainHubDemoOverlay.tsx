/**
 * BrainHubDemoOverlay — full-screen overlay that displays the DDD Brain Hub
 * design mockup (a static HTML bundled at desktop/public/brain-hub-demo.html)
 * inside an iframe.
 *
 * This is a DEMO surface, not a product implementation: it mounts the phase-1
 * Brain Hub visualization mockup so it can be shown inside the running app.
 * It is a pure additive, trivially-removable component — it introduces no new
 * app state and touches no existing view. It mirrors the existing CodeGraph
 * overlay pattern: listen for a `swarm:show-brain-hub` window event, render a
 * `fixed inset-0 z-50` overlay, close on Esc or the close button.
 *
 * To remove after the demo: delete this file, the <BrainHubDemoOverlay/> mount
 * and the nav-brain-hub button in ThreeColumnLayout.tsx, and
 * public/brain-hub-demo.html.
 */

import { useEffect, useState, useCallback } from 'react';

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
        <span className="text-[10px] font-mono text-[#5b636d]">design mockup · phase-1</span>
        <button
          onClick={close}
          data-testid="brain-hub-close"
          className="ml-auto flex items-center gap-1 text-[12px] text-[#8b949e] hover:text-[#e6edf3] px-2 py-1 rounded-md hover:bg-[#1f2630]"
        >
          <span className="material-symbols-outlined text-[16px]">close</span>
          Close
        </button>
      </div>
      <iframe
        src="/brain-hub-demo.html"
        title="Brain Hub demo"
        className="flex-1 w-full border-0 bg-[#0e1117]"
      />
    </div>
  );
}
