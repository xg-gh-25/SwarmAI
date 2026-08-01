/**
 * Tests for DomainStubOverlays — the placeholder fullscreen overlays for A10
 * domains that don't yet have a full surface (Context / Pipeline / Pollinate).
 * Each opens on its `swarm:show-<domain>` window event (same contract as
 * BrainHubDemoOverlay) and renders a labeled skeleton. Cycle-3 scope: every
 * domain card opens SOMETHING; content精修 is a later cycle.
 *
 * NOTE: `history` is NOT a stub — it has a real surface (HistoryOverlay in
 * ChatPage handles swarm:show-history). It was removed from STUBS so both don't
 * open on the same event.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { DomainStubOverlays } from './DomainStubOverlays';

// Modal renders via a portal to document.body; RTL queries find it fine.
afterEach(() => cleanup());

const CASES: Array<[string, string, string]> = [
  ['swarm:show-context', 'stub-overlay-context', 'Context'],
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
    expect(screen.queryByTestId('stub-overlay-context')).toBeNull();
    expect(screen.queryByTestId('stub-overlay-pollinate')).toBeNull();
  });
});
