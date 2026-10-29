/** The Brain Hub surface opens the Library overlay for the General Brain's knowledge:
 *  Library has no show-event, so the surface must pass the provider's openOverlay. It
 *  also gives the Hive run cards inside it (a Brain's Runs tab) ChatPage's opener of a
 *  Hive session, closing the Hub first; without ChatPage's opener there is none. */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { OverlayProvider, useOverlay } from '../../contexts/OverlayContext';
import { useContext } from 'react';
import { getOverlaySpec } from './overlayRegistry';
import { OpenSessionContext } from '../swarm-out/openSession';
import './overlaySurfaces'; // side-effect: register the real surfaces

function HubStub({ onOpenLibrary }: { onOpenLibrary?: () => void }) {
  const open = useContext(OpenSessionContext);
  return (
    <>
      <button data-testid="hub-open-library" onClick={() => onOpenLibrary?.()}>Open Library</button>
      {open && <button data-testid="hub-open-session" onClick={() => open('sess_1', 'Fix · hive-a')}>Open session</button>}
    </>
  );
}
vi.mock('./BrainHub', () => ({ BrainHub: HubStub }));

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

  it('a Hive card inside opens its session with ChatPage\'s opener, after closing the Hub', async () => {
    const order: string[] = [];
    const spec = getOverlaySpec('brain-hub');
    render(
      <OverlayProvider>
        {spec?.render({ close: () => order.push('close'), openHiveSession: (sid: string, t: string) => order.push(`open ${sid} ${t}`) } as never)}
      </OverlayProvider>,
    );
    fireEvent.click(await screen.findByTestId('hub-open-session'));
    expect(order).toEqual(['close', 'open sess_1 Fix · hive-a']);
  });

  it('without ChatPage\'s opener the cards have none', async () => {
    const spec = getOverlaySpec('brain-hub');
    render(<OverlayProvider>{spec?.render({ close: () => undefined } as never)}</OverlayProvider>);
    await screen.findByTestId('hub-open-library');
    expect(screen.queryByTestId('hub-open-session')).toBeNull();
  });
});
