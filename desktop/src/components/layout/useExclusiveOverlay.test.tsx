/**
 * Tests for useExclusiveOverlay — the single-overlay invariant + back-to-chat
 * return path (Gate-2 findings A-1 / F-1, run_1aab916c).
 *
 * Mutation guard: reverting the "close on other show-events" branch makes the
 * stacking test RED; removing the back-to-chat listener makes that test RED.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { useExclusiveOverlay, BACK_TO_CHAT_EVENT } from './useExclusiveOverlay';

function Probe({ event, label }: { event: string; label: string }) {
  const { open } = useExclusiveOverlay(event);
  return open ? <div data-testid={`open-${label}`} /> : null;
}

function fire(name: string) {
  act(() => {
    window.dispatchEvent(new CustomEvent(name));
  });
}

afterEach(cleanup);

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
