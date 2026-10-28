/**
 * hooks/useLayer — who gets Escape, and the Tab trap's ``trap: false``.
 *
 * Escape goes to the topmost layer, but with focus inside that layer the control
 * under focus has the first say: a field that takes Escape for itself (a rename
 * input, a search box) marks it handled and the layer stays open. With focus outside
 * the top layer (a menu opened by the mouse while the terminal keeps focus), the
 * layer takes the key first, before a widget that would swallow it.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { useRef, type ReactNode } from 'react';
import { useEscape, useLayerFocus } from '../useLayer';

afterEach(cleanup);

function Layer({ onEscape, children, withRef = true, testId = 'layer' }: {
  onEscape: () => void; children?: ReactNode; withRef?: boolean; testId?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEscape(true, onEscape, withRef ? ref : undefined);
  return <div ref={ref} data-testid={testId}>{children}</div>;
}

describe('useEscape: the control under focus first, then the top layer', () => {
  it('a field inside the layer that takes Escape keeps it: the layer stays open', () => {
    const close = vi.fn();
    const cancelRename = vi.fn();
    render(<Layer onEscape={close}>
      <input data-testid="rename" onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); cancelRename(); } }} />
    </Layer>);
    fireEvent.keyDown(screen.getByTestId('rename'), { key: 'Escape' });
    expect(cancelRename).toHaveBeenCalledTimes(1);
    expect(close).not.toHaveBeenCalled();
  });

  it('a field that stops the key (stopPropagation) also keeps it', () => {
    const close = vi.fn();
    render(<Layer onEscape={close}>
      <input data-testid="search" onKeyDown={(e) => { if (e.key === 'Escape') e.stopPropagation(); }} />
    </Layer>);
    fireEvent.keyDown(screen.getByTestId('search'), { key: 'Escape' });
    expect(close).not.toHaveBeenCalled();
  });

  it('an Escape nobody inside handled closes the layer, once', () => {
    const close = vi.fn();
    render(<Layer onEscape={close}><button data-testid="ok">OK</button></Layer>);
    fireEvent.keyDown(screen.getByTestId('ok'), { key: 'Escape' });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('only the top layer closes, with focus in either one', () => {
    const lower = vi.fn();
    const upper = vi.fn();
    render(<>
      <Layer onEscape={lower} testId="lower"><button data-testid="in-lower">x</button></Layer>
      <Layer onEscape={upper} testId="upper"><button data-testid="in-upper">y</button></Layer>
    </>);
    fireEvent.keyDown(screen.getByTestId('in-upper'), { key: 'Escape' });
    fireEvent.keyDown(screen.getByTestId('in-lower'), { key: 'Escape' });   // focus left behind
    expect(upper).toHaveBeenCalledTimes(2);
    expect(lower).not.toHaveBeenCalled();
  });

  it('with focus outside it, the layer takes Escape before a widget that swallows it (a terminal)', () => {
    const closeMenu = vi.fn();
    const shell = vi.fn();
    const terminal = document.createElement('textarea');
    terminal.addEventListener('keydown', (e) => { shell(); e.preventDefault(); e.stopPropagation(); });
    document.body.appendChild(terminal);
    render(<Layer onEscape={closeMenu} withRef={false} />);
    fireEvent.keyDown(terminal, { key: 'Escape' });
    expect(closeMenu).toHaveBeenCalledTimes(1);
    expect(shell).not.toHaveBeenCalled();
    terminal.remove();
  });

  it('a layer with a ref also takes Escape first when focus is outside it', () => {
    const close = vi.fn();
    const outside = vi.fn();
    const field = document.createElement('input');
    field.addEventListener('keydown', (e) => { outside(); e.preventDefault(); });
    document.body.appendChild(field);
    render(<Layer onEscape={close}><button>in</button></Layer>);
    fireEvent.keyDown(field, { key: 'Escape' });
    expect(close).toHaveBeenCalledTimes(1);
    expect(outside).not.toHaveBeenCalled();
    field.remove();
  });

  it('a key that ends an input-method composition is not an Escape', () => {
    const close = vi.fn();
    render(<Layer onEscape={close}><input data-testid="ime" /></Layer>);
    fireEvent.keyDown(screen.getByTestId('ime'), { key: 'Escape', isComposing: true });
    fireEvent.keyDown(screen.getByTestId('ime'), { key: 'Escape', keyCode: 229 });
    expect(close).not.toHaveBeenCalled();
  });
});

function Focused({ trap }: { trap?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayerFocus(true, ref, { trap });
  return <div ref={ref} tabIndex={-1}><button>first</button><button>last</button></div>;
}

describe('useLayerFocus: trap: false', () => {
  it('leaves Tab to the browser (the overlay host beside the left nav)', () => {
    render(<Focused trap={false} />);
    const last = screen.getByText('last');
    last.focus();
    expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(true);   // not prevented
    expect(document.activeElement).toBe(last);
  });

  it('by default Tab wraps from the last control to the first', () => {
    render(<Focused />);
    screen.getByText('last').focus();
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByText('first'));
  });
});
