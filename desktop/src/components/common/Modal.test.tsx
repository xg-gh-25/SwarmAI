/**
 * Tests for the shared Modal component — the enter/exit ANIMATION state machine
 * (run_9775fcd8). Modal is now the SMALL centered dialog only: the fullscreen
 * card-detail branch (mode badge, spout, chat-area-bounded scrim, navSource /
 * chatAreaBounds geometry, FS_WIDTH) was RETIRED 2026-08-04 (M5) — every fullscreen
 * surface renders through the OverlayHost subsystem now (OverlayHost.test covers its
 * geometry contract). The tests that exercised the fullscreen branch were removed
 * with it.
 *
 * The animation contract (aligned to layout-A10 mockup):
 *  - `isOpen=false` does NOT instantly unmount — the modal stays in the DOM through a
 *    300ms exit backstop (EXIT_MS) so the close transition can play.
 *  - `return null` gates on internal `isRendering`, NOT `isOpen`.
 *  - Esc + body-scroll-lock gate on `isOpen` (intent), not `isRendering`.
 *  - re-entrancy: isOpen flipping true mid-exit cancels the pending unmount.
 *
 * Uses fake timers to drive the exit backstop deterministically. rAF is stubbed to
 * run synchronously so the enter class applies without a real paint loop.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, act, cleanup, fireEvent } from '@testing-library/react';
import Modal from './Modal';

// Run rAF callbacks synchronously (double-rAF enter would otherwise never fire
// under fake timers). Each call resolves on the next microtask-ish tick.
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    return setTimeout(() => cb(performance.now()), 0) as unknown as number;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id as unknown as ReturnType<typeof setTimeout>));
});

afterEach(() => {
  cleanup();
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.style.overflow = '';
  (window as unknown as { __modalLockCount?: number }).__modalLockCount = 0;
});

function noop() {}

describe('Modal — mount/unmount lifecycle', () => {
  it('renders content when isOpen=true', () => {
    render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.getByText('body')).toBeInTheDocument();
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();
  });

  it('renders nothing when opened false from the start', () => {
    render(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.queryByTestId('modal-scrim')).toBeNull();
  });

  it('does NOT unmount immediately on close — stays through the exit backstop, then unmounts', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    // Still mounted right after close (exit transition playing) AND visually
    // transitioning OUT — the exit target class must be applied, not just "still there".
    const scrim = screen.getByTestId('modal-scrim');
    expect(scrim).toBeInTheDocument();
    expect(scrim.className).toContain('opacity-0');

    // After the 300ms backstop it unmounts
    act(() => { vi.advanceTimersByTime(310); });
    expect(screen.queryByTestId('modal-scrim')).toBeNull();
  });

  it('applies the entered (.open) visual state after the enter rAF frames', () => {
    render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    const scrim = screen.getByTestId('modal-scrim');
    // Enter transition target applied after rAF ticks
    act(() => { vi.advanceTimersByTime(1); });
    expect(scrim.className).toContain('opacity-100');
  });
});

describe('Modal — re-entrancy (open → close → open < backstop)', () => {
  it('cancels the pending unmount when reopened mid-exit (does not vanish)', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    act(() => { vi.advanceTimersByTime(100); }); // partway through exit

    // Reopen before the 300ms backstop fires
    rerender(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    act(() => { vi.advanceTimersByTime(300); }); // the OLD timer would have unmounted here

    // Still present — the stale unmount was cancelled
    expect(screen.getByTestId('modal-scrim')).toBeInTheDocument();
  });
});

describe('Modal — Esc + scroll lock (gate on isOpen)', () => {
  it('calls onClose on Escape while open', () => {
    const onClose = vi.fn();
    render(<Modal isOpen onClose={onClose} title="T"><div>body</div></Modal>);
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does NOT call onClose on Escape during the exit window (listener gated on isOpen)', () => {
    const onClose = vi.fn();
    const { rerender } = render(<Modal isOpen onClose={onClose} title="T"><div>body</div></Modal>);
    rerender(<Modal isOpen={false} onClose={onClose} title="T"><div>body</div></Modal>);
    // still mounted (exit playing) but Esc listener was removed with isOpen=false
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(onClose).not.toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(310); });
  });

  it('locks body scroll while open and restores it after full unmount', () => {
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><div>body</div></Modal>);
    expect(document.body.style.overflow).toBe('hidden');

    rerender(<Modal isOpen={false} onClose={noop} title="T"><div>body</div></Modal>);
    // lock released on isOpen=false (ref-count → 0)
    expect(document.body.style.overflow).toBe('');
    act(() => { vi.advanceTimersByTime(310); });
  });
});

describe('Modal — a layer: focus, Tab, Escape (hooks/useLayer)', () => {
  it('is a labelled modal dialog, focus moves in, Tab stays inside, and focus returns to the opener on close', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = render(<Modal isOpen onClose={noop} title="Rename"><input data-testid="a" /><button>OK</button></Modal>);
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(screen.getByRole('dialog', { name: 'Rename' })).toBe(dialog);
    expect(dialog.contains(document.activeElement)).toBe(true);
    screen.getByText('OK').focus();                       // the last control
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'common.button.close' }));
    rerender(<Modal isOpen={false} onClose={noop} title="Rename"><input data-testid="a" /><button>OK</button></Modal>);
    expect(document.activeElement).toBe(opener);           // at once, while the exit plays
    opener.remove();
  });

  it('a field with autoFocus keeps the focus, and focus still returns to the opener on close', () => {
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { rerender } = render(<Modal isOpen onClose={noop} title="T"><input data-testid="name" autoFocus /></Modal>);
    expect(document.activeElement).toBe(screen.getByTestId('name'));
    rerender(<Modal isOpen={false} onClose={noop} title="T"><input data-testid="name" autoFocus /></Modal>);
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('with a dialog open over another, Tab stays inside the upper one', () => {
    render(<>
      <Modal isOpen onClose={noop} title="Lower"><button>Lower OK</button></Modal>
      <Modal isOpen onClose={noop} title="Upper"><button>Upper A</button><button>Upper B</button></Modal>
    </>);
    const upper = screen.getByRole('dialog', { name: 'Upper' });
    // From a middle control the trap stays out of the way: the browser moves focus
    // (not prevented), and nothing pulls it anywhere — the lower dialog's trap least.
    const a = screen.getByText('Upper A');
    a.focus();
    expect(fireEvent.keyDown(a, { key: 'Tab' })).toBe(true);
    expect(document.activeElement).toBe(a);
    expect(fireEvent.keyDown(a, { key: 'Tab', shiftKey: true })).toBe(true);
    expect(document.activeElement).toBe(a);
    screen.getByText('Upper B').focus();                                          // the last control
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    expect(upper.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).not.toBe(screen.getByText('Lower OK'));
  });

  it('Escape closes the dialog and stops there: an overlay behind it (listening on document) is not closed', () => {
    const onClose = vi.fn();
    const host = vi.fn();
    document.addEventListener('keydown', host);
    render(<Modal isOpen onClose={onClose} title="T"><input data-testid="x" /></Modal>);
    fireEvent.keyDown(screen.getByTestId('x'), { key: 'Escape' });
    document.removeEventListener('keydown', host);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(host).not.toHaveBeenCalled();
  });

  it('with two dialogs open, Escape closes only the top one', () => {
    const lower = vi.fn();
    const upper = vi.fn();
    render(<><Modal isOpen onClose={lower} title="Lower"><span /></Modal><Modal isOpen onClose={upper} title="Upper"><span /></Modal></>);
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(upper).toHaveBeenCalledTimes(1);
    expect(lower).not.toHaveBeenCalled();
  });
});
