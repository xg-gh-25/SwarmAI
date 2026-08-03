/**
 * Tests for useExclusiveOverlay — the single-overlay invariant + back-to-chat
 * return path (Gate-2 findings A-1 / F-1, run_1aab916c).
 *
 * Mutation guard: reverting the "close on other show-events" branch makes the
 * stacking test RED; removing the back-to-chat listener makes that test RED.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import {
  useExclusiveOverlay,
  useActiveOverlayEvent,
  clearActiveOverlayEvent,
  closeOpenOverlays,
  __resetActiveOverlayEvent,
  BACK_TO_CHAT_EVENT,
} from './useExclusiveOverlay';

function Probe({ event, label }: { event: string; label: string }) {
  const { open, close } = useExclusiveOverlay(event);
  return open ? <button data-testid={`open-${label}`} onClick={close} /> : null;
}

/** Renders the active-overlay-event value + one overlay so we can assert the
 *  highlight source tracks open/close/switch correctly. */
function ActiveProbe({ event }: { event: string }) {
  const active = useActiveOverlayEvent();
  const { open, close } = useExclusiveOverlay(event);
  return (
    <div>
      <div data-testid="active">{active ?? 'null'}</div>
      {open ? <button data-testid="close-btn" onClick={close} /> : null}
    </div>
  );
}

function fire(name: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent(name));
  });
}

afterEach(() => {
  cleanup();
  __resetActiveOverlayEvent(); // module-level state leaks across a file's tests
});

describe('useExclusiveOverlay', () => {
  it('opens on its own show-event', () => {
    render(<Probe event="swarm:show-context" label="ctx" />);
    expect(screen.queryByTestId('open-ctx')).toBeNull();
    fire('swarm:show-context');
    expect(screen.getByTestId('open-ctx')).toBeTruthy();
  });

  it('closes when ANOTHER show-event fires (single-overlay invariant / F-1)', () => {
    render(
      <>
        <Probe event="swarm:show-context" label="ctx" />
        <Probe event="swarm:show-pipeline" label="pipe" />
      </>,
    );
    fire('swarm:show-context');
    expect(screen.getByTestId('open-ctx')).toBeTruthy();

    // Opening pipeline must close context — no stacking.
    fire('swarm:show-pipeline');
    expect(screen.getByTestId('open-pipe')).toBeTruthy();
    expect(screen.queryByTestId('open-ctx')).toBeNull();
  });

  it('closes on swarm:back-to-chat (Chat hero return path / A-1)', () => {
    render(<Probe event="swarm:show-swarmws" label="ws" />);
    fire('swarm:show-swarmws');
    expect(screen.getByTestId('open-ws')).toBeTruthy();

    fire(BACK_TO_CHAT_EVENT);
    expect(screen.queryByTestId('open-ws')).toBeNull();
  });
});

describe('useActiveOverlayEvent (nav card active highlight, run_ad7b32f6)', () => {
  it('starts null and tracks the open overlay event', () => {
    render(<ActiveProbe event="swarm:show-context" />);
    expect(screen.getByTestId('active').textContent).toBe('null');
    fire('swarm:show-context');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-context');
  });

  it('switches active to the newly-opened overlay (single-highlight)', () => {
    // Production mounts ALL overlays at once (SwarmWSOverlay/BrainHub/DomainStubs
    // are unconditionally rendered) — each overlay's own `show` handler is what
    // re-points activeEvent to itself. Mount both so the switch is realistic.
    render(
      <>
        <ActiveProbe event="swarm:show-context" />
        <Probe event="swarm:show-pipeline" label="pipe" />
      </>,
    );
    fire('swarm:show-context');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-context');
    // Opening a different (mounted) overlay re-points the active event.
    fire('swarm:show-pipeline');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-pipeline');
  });

  // Mutation guard: if close() stops clearing activeEvent, this goes RED — the
  // exact staleness hole Gate-1 flagged (Esc/backdrop/file-open leave it lit).
  it('close() clears the active event (no stale highlight)', () => {
    render(<ActiveProbe event="swarm:show-swarmws" />);
    fire('swarm:show-swarmws');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-swarmws');
    act(() => {
      fireEvent.click(screen.getByTestId('close-btn'));
    });
    expect(screen.getByTestId('active').textContent).toBe('null');
  });

  it('back-to-chat clears the active event', () => {
    render(<ActiveProbe event="swarm:show-brain-hub" />);
    fire('swarm:show-brain-hub');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-brain-hub');
    fire(BACK_TO_CHAT_EVENT);
    expect(screen.getByTestId('active').textContent).toBe('null');
  });

  it('clearActiveOverlayEvent() clears it (non-window surface takes over)', () => {
    render(<ActiveProbe event="swarm:show-pollinate" />);
    fire('swarm:show-pollinate');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-pollinate');
    act(() => {
      clearActiveOverlayEvent();
    });
    expect(screen.getByTestId('active').textContent).toBe('null');
  });
});

describe('closeOpenOverlays — cross-mechanism mutual exclusion (run_9f8b6c21)', () => {
  // Mutation guard: if closeOpenOverlays stops broadcasting back-to-chat, an open
  // overlay survives when a modal/deep-link takes over → the stacking bug returns.
  it('closes an open overlay + clears the active highlight', () => {
    render(<ActiveProbe event="swarm:show-context" />);
    fire('swarm:show-context');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-context');
    expect(screen.getByTestId('close-btn')).toBeTruthy();

    act(() => { closeOpenOverlays(); });

    // overlay closed (no close-btn) AND highlight cleared — the modal-takeover fix.
    expect(screen.queryByTestId('close-btn')).toBeNull();
    expect(screen.getByTestId('active').textContent).toBe('null');
  });

  // Library is NOT in ALL_SHOW_EVENTS (banned from the agent UI-action allowlist
  // SSOT), so peers' `others` sets cannot close it — closeOpenOverlays MUST reach it
  // via the back-to-chat broadcast. This is the exact asymmetry Gate-0 caught.
  it('closes a Library overlay even though swarm:show-library is not in ALL_SHOW_EVENTS', () => {
    render(<Probe event="swarm:show-library" label="lib" />);
    fire('swarm:show-library');
    expect(screen.getByTestId('open-lib')).toBeTruthy();

    act(() => { closeOpenOverlays(); });
    expect(screen.queryByTestId('open-lib')).toBeNull();
  });

  // The chokepoint contract: closeOpenOverlays() then a target show-event ends with
  // ONLY the target open + active = target (synchronous dispatch ordering holds).
  it('leaves exactly the target open when used as an open chokepoint', () => {
    render(
      <>
        <ActiveProbe event="swarm:show-context" />
        <Probe event="swarm:show-pipeline" label="pipe" />
      </>,
    );
    fire('swarm:show-context');
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-context');

    // Simulate showOverlay's chokepoint: close all, then dispatch the target.
    act(() => {
      closeOpenOverlays();
      window.dispatchEvent(new CustomEvent('swarm:show-pipeline'));
    });
    expect(screen.getByTestId('open-pipe')).toBeTruthy();
    expect(screen.getByTestId('active').textContent).toBe('swarm:show-pipeline');
  });
});
