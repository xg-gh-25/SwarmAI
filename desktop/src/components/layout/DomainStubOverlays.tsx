/**
 * DomainStubOverlays — placeholder fullscreen overlays for the A10 domains that
 * don't yet have a full product surface (Context / Pipeline / Pollinate /
 * History). Each listens for its `swarm:show-<domain>` window event (the same
 * contract as BrainHubDemoOverlay / SwarmWSOverlay) and renders a labeled
 * skeleton in the shared common/<Modal size="fullscreen"/>.
 *
 * Cycle-3 scope (run_1aab916c): every A10 nav card must open SOMETHING so the
 * navigation is coherent end-to-end. The real per-domain content is filled in by
 * later per-card cycles — this file is the seam that keeps those changes local.
 */
import { type ReactNode } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';

interface StubDef {
  key: string;
  event: string;
  title: string;
  blurb: string;
}

const STUBS: StubDef[] = [
  // NOTE: `context` is NO LONGER a stub — swarm:show-context now opens the real
  // CMBrainOverlay (C&M Global Brain, run_5f7d4fe1), rendered in ThreeColumnLayout.
  // It must NOT also be a stub here, or both would open on the same event (double
  // fullscreen overlay) — same contract as the `history` note below.
  // NOTE: `pipeline` is NO LONGER a stub — swarm:show-pipeline now opens the real
  // PipelineOverlay (retro-analytics dashboard, run_f8494370), rendered in ChatPage.
  // It must NOT also be a stub here, or both would open on the same event (double
  // fullscreen overlay) — same contract as the `context`/`history` notes.
  // NOTE: `pollinate` is NO LONGER a stub — swarm:show-pollinate now opens the real
  // PollinateOverlay (content-asset gallery, run_ea7c5fbc), rendered in ChatPage.
  // Same double-overlay contract as pipeline/context/history.
  // NOTE: `history` is NOT a stub — it has a real surface. The left-nav History
  // row's `swarm:show-history` event is handled by HistoryOverlay (rendered in
  // ChatPage), which hosts the searchable HistoryView. It must NOT also be a stub
  // here, or both would open on the same event (double fullscreen overlay).
];

function StubOverlay({ def }: { def: StubDef }): ReactNode {
  const { open, close } = useExclusiveOverlay(def.event);

  return (
    <Modal isOpen={open} onClose={close} title={def.title} size="fullscreen" mode={def.key.toUpperCase()} fullscreenWidth="m">
      <div
        className="flex-1 flex flex-col items-center justify-center gap-3 p-10 text-center"
        data-testid={`stub-overlay-${def.key}`}
      >
        <div className="text-lg font-semibold text-[var(--color-text)]">{def.title}</div>
        <p className="max-w-md text-sm text-[var(--color-text-muted)]">{def.blurb}</p>
        <span className="text-[11px] font-mono uppercase tracking-widest text-[var(--color-text-faint)]">
          placeholder
        </span>
      </div>
    </Modal>
  );
}

export function DomainStubOverlays() {
  return (
    <>
      {STUBS.map((def) => (
        <StubOverlay key={def.key} def={def} />
      ))}
    </>
  );
}
