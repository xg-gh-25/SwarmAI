/**
 * NewBrainOverlay interaction tests — lock the launcher's contract:
 *   1. Opens on swarm:show-new-brain.
 *   2. Collects name + governs + starter items (auto-classified by type, pill correctable).
 *   3. Create builds ONE manifest, calls onDispatch(prompt), and closes ON SUCCESS.
 *   4. F4: when onDispatch returns FALSE (all tabs busy / draft guard), the
 *      launcher stays OPEN (no close) so the toast is visible.
 *   5. Create is disabled until a name is entered.
 *
 * requestAnimationFrame is stubbed to run synchronously so the double-rAF close
 * is observable in the test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { NewBrainContent } from '../NewBrainOverlay';

// M3: NewBrainOverlay → NewBrainContent (OverlayHost registry). Content renders
// immediately (host-owned open + fresh mount per open); `close` is now a prop (was
// useExclusiveOverlay). Helper renders it with a stub close + the given onDispatch.
function renderContent(onDispatch: (p: string) => boolean, close = () => {}) {
  return render(<NewBrainContent onDispatch={onDispatch} close={close} />);
}
function openOverlay() { /* no-op: NewBrainContent renders immediately (host-owned open) */ }

beforeEach(() => {
  // Run rAF synchronously so the double-rAF close resolves within the test.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  // Ensure the shared overlay mux is closed between tests.
  window.dispatchEvent(new CustomEvent('swarm:back-to-chat'));
});

describe('NewBrainOverlay', () => {
  it('renders the launcher', async () => {
    renderContent(() => true);
    expect(await screen.findByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('Create is disabled with no name, enabled once named', async () => {
    renderContent(() => true);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const create = screen.getByTestId('new-brain-create') as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'Acme Payments' } });
    expect(create.disabled).toBe(false);
  });

  it('auto-classifies added items by type into GOVERN/DISTILL/SHELF', async () => {
    renderContent(() => true);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const input = screen.getByTestId('new-brain-material-input');

    fireEvent.change(input, { target: { value: 'github.com/acme/payments' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.change(input, { target: { value: '~/work/acme/notes/' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(await screen.findByText('GOVERN ▾')).toBeTruthy(); // repo
    expect(await screen.findByText('SHELF ▾')).toBeTruthy();  // folder
  });

  it('Create dispatches ONE manifest containing the collected data, then closes', async () => {
    const onDispatch = vi.fn(() => true);
    const close = vi.fn();
    renderContent(onDispatch, close);
    await screen.findByTestId('new-brain-overlay');

    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'Acme Payments' } });
    fireEvent.click(screen.getByTestId('new-brain-governs-codebase'));
    const input = screen.getByTestId('new-brain-material-input');
    fireEvent.change(input, { target: { value: 'github.com/acme/payments' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    fireEvent.click(screen.getByTestId('new-brain-create'));

    expect(onDispatch).toHaveBeenCalledTimes(1);
    // closed on success — host's close() called (was: overlay self-unmounts)
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    const prompt = onDispatch.mock.calls[0][0] as string;
    expect(prompt).toContain('"Acme Payments"');
    expect(prompt).toContain('governs a codebase');
    expect(prompt).toContain('github.com/acme/payments');
    expect(prompt).toContain('s_project-manager');
  });

  it('F4: does NOT close when onDispatch returns false (no tab landed)', async () => {
    const onDispatch = vi.fn(() => false);
    const close = vi.fn();
    renderContent(onDispatch, close);
    await screen.findByTestId('new-brain-overlay');

    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'X' } });
    fireEvent.click(screen.getByTestId('new-brain-create'));

    expect(onDispatch).toHaveBeenCalledTimes(1);
    // must NOT close on a failed dispatch — launcher stays so the toast is visible
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('F4/#13: a THROWING onDispatch keeps the launcher open (no dead-end)', async () => {
    const onDispatch = vi.fn(() => { throw new Error('addTab blew up'); });
    const close = vi.fn();
    renderContent(onDispatch, close);
    await screen.findByTestId('new-brain-overlay');
    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'X' } });
    // must NOT throw out of the click handler, must NOT close
    expect(() => fireEvent.click(screen.getByTestId('new-brain-create'))).not.toThrow();
    expect(onDispatch).toHaveBeenCalledTimes(1);
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('#1: an internal Explorer drag (application/json FileTreeItem) adds a real-path item', async () => {
    renderContent(() => true);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const zone = screen.getByTestId('new-brain-dropzone');

    const fileData = { name: 'payments', type: 'directory', path: '/Users/me/work/acme/payments' };
    const dt = {
      getData: (t: string) => (t === 'application/json' ? JSON.stringify(fileData) : ''),
      files: [] as File[],
      types: ['application/json'],
    };
    fireEvent.drop(zone, { dataTransfer: dt });

    // real workspace path landed, and a directory → SHELF (not mis-read by detectKind)
    expect(await screen.findByText('/Users/me/work/acme/payments')).toBeTruthy();
    expect(await screen.findByText('SHELF ▾')).toBeTruthy();
  });

  it('#1: an internal file drag classifies by type (repo/doc → GOVERN/DISTILL)', async () => {
    renderContent(() => true);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const zone = screen.getByTestId('new-brain-dropzone');
    const fileData = { name: 'decisions.pdf', type: 'file', path: '/abs/decisions.pdf' };
    fireEvent.drop(zone, {
      dataTransfer: {
        getData: (t: string) => (t === 'application/json' ? JSON.stringify(fileData) : ''),
        files: [], types: ['application/json'],
      },
    });
    expect(await screen.findByText('/abs/decisions.pdf')).toBeTruthy();
    expect(await screen.findByText('DISTILL ▾')).toBeTruthy();
  });

  it('#3: Create WITHOUT pressing Enter still includes the typed draft (blur-commit)', async () => {
    const onDispatch = vi.fn(() => true);
    renderContent(onDispatch);
    await screen.findByTestId('new-brain-overlay');
    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'Acme' } });
    const input = screen.getByTestId('new-brain-material-input');
    // type but do NOT press Enter — rely on onBlur committing before Create reads items
    fireEvent.change(input, { target: { value: 'github.com/acme/x' } });
    fireEvent.blur(input);
    fireEvent.click(screen.getByTestId('new-brain-create'));
    const prompt = onDispatch.mock.calls[0][0] as string;
    expect(prompt).toContain('github.com/acme/x');
  });

  // #4b-regression (fresh-birth-on-reopen) RETIRED (M3): the old reset-on-raw-event
  // hack existed because the legacy overlay stayed mounted and `open` didn't observably
  // transition on rapid reopen. In the OverlayHost model the host UNMOUNTS the surface
  // on close and MOUNTS it fresh on open, so component-local state starts empty every
  // time by construction. The mount/unmount lifecycle is covered by OverlayHost.test.
  // A direct-render test here can no longer exercise "reopen" (there is no host).

  it('a role pill cycles GOVERN→DISTILL→SHELF on click (user override)', async () => {
    renderContent(() => true);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const input = screen.getByTestId('new-brain-material-input');
    fireEvent.change(input, { target: { value: 'github.com/acme/payments' } }); // → GOVERN
    fireEvent.keyDown(input, { key: 'Enter' });

    const pill = await screen.findByText('GOVERN ▾');
    fireEvent.click(pill);
    expect(await screen.findByText('DISTILL ▾')).toBeTruthy();
  });
});
