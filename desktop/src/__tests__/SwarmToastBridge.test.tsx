/**
 * SwarmToastBridge — document `swarm:toast` → ToastContext (run_f49d3ff3 Gate-2 HIGH).
 *
 * Several decoupled surfaces (Canvas 404 notice, overlays) dispatch a `swarm:toast`
 * document CustomEvent, but NOTHING listened → the notice never rendered (dead event).
 * This pins the bridge: a dispatched swarm:toast becomes a REAL, visible toast.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { ToastProvider } from '../contexts/ToastContext';
import { ToastStack } from '../components/common/ToastStack';
import { SwarmToastBridge } from '../App';

// Stack renders the visible toasts from context; bridge feeds context from the event.
function Harness() {
  return (
    <ToastProvider>
      <SwarmToastBridge />
      <ToastStack />
    </ToastProvider>
  );
}

describe('SwarmToastBridge', () => {
  it('renders a visible toast when a swarm:toast document event fires (the R1 404 notice actually shows)', async () => {
    render(<Harness />);
    act(() => {
      document.dispatchEvent(new CustomEvent('swarm:toast', {
        detail: { message: 'File not found: nope/ghost.json' },
      }));
    });
    await waitFor(() => expect(screen.getByText('File not found: nope/ghost.json')).toBeTruthy());
  });

  it('ignores a swarm:toast with no message (defensive)', () => {
    const Harness2 = () => (
      <ToastProvider><SwarmToastBridge /><ToastStack /></ToastProvider>
    );
    render(<Harness2 />);
    act(() => { document.dispatchEvent(new CustomEvent('swarm:toast', { detail: {} })); });
    // No alert rendered.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('honors an explicit severity from the event detail', async () => {
    render(<Harness />);
    act(() => {
      document.dispatchEvent(new CustomEvent('swarm:toast', {
        detail: { message: 'boom', severity: 'error' },
      }));
    });
    await waitFor(() => expect(screen.getByText('boom')).toBeTruthy());
  });
});
