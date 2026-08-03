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
import { NewBrainOverlay } from '../NewBrainOverlay';

function openOverlay() {
  window.dispatchEvent(new CustomEvent('swarm:show-new-brain'));
}

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
  it('opens on swarm:show-new-brain and shows the launcher', async () => {
    render(<NewBrainOverlay onDispatch={() => true} />);
    expect(screen.queryByTestId('new-brain-overlay')).toBeNull();
    openOverlay();
    expect(await screen.findByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('Create is disabled with no name, enabled once named', async () => {
    render(<NewBrainOverlay onDispatch={() => true} />);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    const create = screen.getByTestId('new-brain-create') as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'Acme Payments' } });
    expect(create.disabled).toBe(false);
  });

  it('auto-classifies added items by type into GOVERN/DISTILL/SHELF', async () => {
    render(<NewBrainOverlay onDispatch={() => true} />);
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
    render(<NewBrainOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');

    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'Acme Payments' } });
    fireEvent.click(screen.getByTestId('new-brain-governs-codebase'));
    const input = screen.getByTestId('new-brain-material-input');
    fireEvent.change(input, { target: { value: 'github.com/acme/payments' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    fireEvent.click(screen.getByTestId('new-brain-create'));

    expect(onDispatch).toHaveBeenCalledTimes(1);
    const prompt = onDispatch.mock.calls[0][0] as string;
    expect(prompt).toContain('"Acme Payments"');
    expect(prompt).toContain('governs a codebase');
    expect(prompt).toContain('github.com/acme/payments');
    expect(prompt).toContain('s_project-manager');

    // closed on success — overlay unmounts
    await waitFor(() => expect(screen.queryByTestId('new-brain-overlay')).toBeNull());
  });

  it('F4: stays OPEN when onDispatch returns false (no tab landed)', async () => {
    const onDispatch = vi.fn(() => false);
    render(<NewBrainOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');

    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'X' } });
    fireEvent.click(screen.getByTestId('new-brain-create'));

    expect(onDispatch).toHaveBeenCalledTimes(1);
    // still open — the launcher must not close on a failed dispatch
    expect(screen.getByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('F4/#13: a THROWING onDispatch keeps the launcher open (no dead-end)', async () => {
    const onDispatch = vi.fn(() => { throw new Error('addTab blew up'); });
    render(<NewBrainOverlay onDispatch={onDispatch} />);
    openOverlay();
    await screen.findByTestId('new-brain-overlay');
    fireEvent.change(screen.getByTestId('new-brain-name'), { target: { value: 'X' } });
    // must NOT throw out of the click handler, must NOT close
    expect(() => fireEvent.click(screen.getByTestId('new-brain-create'))).not.toThrow();
    expect(onDispatch).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('new-brain-overlay')).toBeTruthy();
  });

  it('#1: an internal Explorer drag (application/json FileTreeItem) adds a real-path item', async () => {
    render(<NewBrainOverlay onDispatch={() => true} />);
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
    render(<NewBrainOverlay onDispatch={() => true} />);
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
    render(<NewBrainOverlay onDispatch={onDispatch} />);
    openOverlay();
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

  it('a role pill cycles GOVERN→DISTILL→SHELF on click (user override)', async () => {
    render(<NewBrainOverlay onDispatch={() => true} />);
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
