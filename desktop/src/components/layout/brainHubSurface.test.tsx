/** The Brain Hub surface opens the Library overlay for the General Brain's knowledge:
 *  Library has no show-event, so the surface must pass the provider's openOverlay. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OverlayProvider, useOverlay } from '../../contexts/OverlayContext';
import { getOverlaySpec } from './overlayRegistry';
import './overlaySurfaces'; // side-effect: register the real surfaces

vi.mock('./BrainHub', () => ({
  BrainHub: ({ onOpenLibrary }: { onOpenLibrary?: () => void }) =>
    <button data-testid="hub-open-library" onClick={() => onOpenLibrary?.()}>Open Library</button>,
}));

function Probe() {
  const { activeOverlay } = useOverlay();
  return <div data-testid="active">{activeOverlay ?? 'none'}</div>;
}

describe('the brain-hub surface', () => {
  it("switches to the Library overlay when the Hub asks for it", async () => {
    const spec = getOverlaySpec('brain-hub');
    render(
      <OverlayProvider>
        <Probe />
        {spec?.render({ close: () => undefined, dispatchPrompt: undefined } as never)}
      </OverlayProvider>,
    );
    fireEvent.click(await screen.findByTestId('hub-open-library'));
    await waitFor(() => expect(screen.getByTestId('active').textContent).toBe('library'));
  });
});
