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
  {
    key: 'context',
    event: 'swarm:show-context',
    title: 'Context',
    blurb: 'What is loaded into the prompt right now — the 11 context files + live recall. Full view coming in a later cycle.',
  },
  {
    key: 'pipeline',
    event: 'swarm:show-pipeline',
    title: 'Pipeline',
    blurb: 'Code-delivery runs (EVALUATE→REFLECT), live status, and history. Full view coming in a later cycle.',
  },
  {
    key: 'pollinate',
    event: 'swarm:show-pollinate',
    title: 'Pollinate',
    blurb: 'Media-delivery packages — poster / video / narrative / shorts. Full view coming in a later cycle.',
  },
  {
    key: 'history',
    event: 'swarm:show-history',
    title: 'History',
    blurb: 'Past conversations beyond the open tabs (90d, FTS5-searchable). Full view coming in a later cycle.',
  },
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
