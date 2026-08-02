/**
 * Tests for DomainStubOverlays — the placeholder fullscreen overlays for A10
 * domains that don't yet have a full surface.
 *
 * As of run_ea7c5fbc, STUBS is EMPTY: every A10 domain now has a real overlay.
 *  - context  → CMBrainOverlay (swarm:show-context, run_5f7d4fe1)
 *  - pipeline → PipelineOverlay (swarm:show-pipeline, run_f8494370)
 *  - pollinate→ PollinateOverlay (swarm:show-pollinate, run_ea7c5fbc)
 *  - history  → HistoryOverlay (swarm:show-history)
 * None must open a STUB here, or a real overlay + a stub would open on the same
 * event (double fullscreen overlay).
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import { DomainStubOverlays } from './DomainStubOverlays';

// Modal renders via a portal to document.body; RTL queries find it fine.
afterEach(() => cleanup());

// Every A10 domain now has a real overlay elsewhere — NONE should be a stub here.
const NO_LONGER_STUBS: Array<[string, string]> = [
  ['swarm:show-pollinate', 'stub-overlay-pollinate'],
  ['swarm:show-pipeline', 'stub-overlay-pipeline'],
  ['swarm:show-context', 'stub-overlay-context'],
  ['swarm:show-history', 'stub-overlay-history'],
];

describe('DomainStubOverlays', () => {
  it('renders nothing on mount (no stubs remain)', () => {
    const { container } = render(<DomainStubOverlays />);
    // The fragment renders no children — no stub overlay markup.
    expect(container.querySelector('[data-testid^="stub-overlay-"]')).toBeNull();
  });

  it.each(NO_LONGER_STUBS)('does NOT open a stub for %s (real overlay owns it)', (evt, testid) => {
    render(<DomainStubOverlays />);
    act(() => {
      window.dispatchEvent(new CustomEvent(evt));
    });
    expect(screen.queryByTestId(testid)).toBeNull();
  });
});
