/**
 * Tests for DomainStubOverlays — the placeholder fullscreen overlays for A10
 * domains that don't yet have a full surface (Context / Pipeline / Pollinate).
 * Each opens on its `swarm:show-<domain>` window event (same contract as
 * BrainHubDemoOverlay) and renders a labeled skeleton. Cycle-3 scope: every
 * domain card opens SOMETHING; content精修 is a later cycle.
 *
 * NOTE: `history` and `context` are NOT stubs — they have real surfaces
 * (HistoryOverlay handles swarm:show-history; CMBrainOverlay handles
 * swarm:show-context, run_5f7d4fe1). Both were removed from STUBS so a real
 * overlay and a stub never open on the same event.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { DomainStubOverlays } from './DomainStubOverlays';

// Modal renders via a portal to document.body; RTL queries find it fine.
afterEach(() => cleanup());

const CASES: Array<[string, string, string]> = [
  ['swarm:show-pipeline', 'stub-overlay-pipeline', 'Pipeline'],
  ['swarm:show-pollinate', 'stub-overlay-pollinate', 'Pollinate'],
];

describe('DomainStubOverlays', () => {
  it('renders nothing until an event fires', () => {
    render(<DomainStubOverlays />);
    for (const [, testid] of CASES) {
      expect(screen.queryByTestId(testid)).toBeNull();
    }
  });

  it.each(CASES)('opens the %s overlay on its window event', (evt, testid, label) => {
    render(<DomainStubOverlays />);
    expect(screen.queryByTestId(testid)).toBeNull();
    act(() => {
      window.dispatchEvent(new CustomEvent(evt));
    });
    const overlay = screen.getByTestId(testid);
    expect(overlay).toBeInTheDocument();
    expect(overlay.textContent).toContain(label);
  });

  it('opening one overlay does not open the others', () => {
    render(<DomainStubOverlays />);
    act(() => {
      window.dispatchEvent(new CustomEvent('swarm:show-pipeline'));
    });
    expect(screen.getByTestId('stub-overlay-pipeline')).toBeInTheDocument();
    expect(screen.queryByTestId('stub-overlay-pollinate')).toBeNull();
    // context is no longer a stub at all (real CMBrainOverlay) — never present here
    expect(screen.queryByTestId('stub-overlay-context')).toBeNull();
  });
});
