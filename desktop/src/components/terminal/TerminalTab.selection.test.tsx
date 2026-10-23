/**
 * Tests for TerminalTab's selection features (docs/design/terminal-locations.md):
 * ⌘C copies through the app's clipboard command and ⌘A / ⌘K act, all swallowed;
 * Ctrl-C / Ctrl-K and plain keys reach the shell; ⌥-drag selects in a mouse-mode
 * program (macOptionClickForcesSelection); WebGL renders and a lost context goes
 * back to the DOM renderer; right-click opens the menu, whose Copy / Add to chat /
 * Select all / Clear act and close it.
 *
 * xterm and the WebGL addon are mocked at the module boundary; the component is real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';

type KeyHandler = (e: KeyboardEvent) => boolean;
let term: {
  keyHandler: KeyHandler | null; opts: Record<string, unknown>; selection: string;
  selectAll: ReturnType<typeof vi.fn>; clear: ReturnType<typeof vi.fn>; loadAddon: ReturnType<typeof vi.fn>;
} | null = null;

vi.mock('@xterm/xterm', () => {
  class Terminal {
    keyHandler: KeyHandler | null = null;
    selection = '';
    opts: Record<string, unknown>;
    cols = 80; rows = 24;
    options: Record<string, unknown> = {};
    loadAddon = vi.fn();
    selectAll = vi.fn();
    clear = vi.fn();
    buffer = { active: { length: 0, getLine: () => null } };
    constructor(opts: Record<string, unknown>) { this.opts = opts; term = this as never; }
    open = vi.fn(); write = vi.fn(); dispose = vi.fn(); focus = vi.fn();
    onData = vi.fn(() => ({ dispose: vi.fn() }));
    onSelectionChange = vi.fn(() => ({ dispose: vi.fn() }));
    attachCustomKeyEventHandler(h: KeyHandler) { this.keyHandler = h; }
    hasSelection() { return this.selection.length > 0; }
    getSelection() { return this.selection; }
  }
  return { Terminal };
});
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = vi.fn(); } }));
vi.mock('@xterm/xterm/css/xterm.css', () => ({}));
let lossCb: (() => void) | null = null;
const glDispose = vi.fn();
vi.mock('@xterm/addon-webgl', () => ({
  WebglAddon: class {
    dispose = glDispose;
    onContextLoss(cb: () => void) { lossCb = cb; return { dispose: vi.fn() }; }
  },
}));
const copyMock = vi.fn(async (_t: string) => true);
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: (t: string) => copyMock(t) }));

import TerminalTab from './TerminalTab';
import type { TerminalTab as TerminalTabModel } from '../../stores/TerminalStore';

const tab = (): TerminalTabModel => ({
  id: 'term-1', title: 'zsh', locKey: '/r/swarmai', slot: 1, cwd: '/r/swarmai', status: 'running',
  pty: { pid: 1, onData: vi.fn(() => ({ dispose: vi.fn() })), onExit: vi.fn(() => ({ dispose: vi.fn() })),
         write: vi.fn(), resize: vi.fn(), kill: vi.fn() },
  getBuffer: () => 'last lines',
});

const key = (k: string, mods: Partial<KeyboardEvent> = {}) =>
  ({ type: 'keydown', key: k, metaKey: false, ctrlKey: false, altKey: false, ...mods }) as KeyboardEvent;

async function mount() {
  const t = tab();
  render(<TerminalTab tab={t} active={true} />);
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });   // init + webgl chunk
  return t;
}

describe('TerminalTab selection', () => {
  beforeEach(() => {
    term = null; lossCb = null; glDispose.mockClear(); copyMock.mockClear();
    vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); disconnect = vi.fn(); });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('⌥-drag selects in mouse-mode programs; WebGL renders, a lost context falls back', async () => {
    await mount();
    expect(term!.opts.macOptionClickForcesSelection).toBe(true);
    expect(term!.loadAddon).toHaveBeenCalledTimes(2);   // fit + webgl
    expect(lossCb).toBeTypeOf('function');
    lossCb!();
    expect(glDispose).toHaveBeenCalledTimes(1);
  });

  it('⌘C copies the selection, ⌘A selects all, ⌘K clears — all kept from the shell', async () => {
    await mount();
    const h = term!.keyHandler!;
    term!.selection = 'FAIL x';
    expect(h(key('c', { metaKey: true }))).toBe(false);
    expect(copyMock).toHaveBeenCalledWith('FAIL x');
    expect(h(key('a', { metaKey: true }))).toBe(false);
    expect(term!.selectAll).toHaveBeenCalled();
    expect(h(key('k', { metaKey: true }))).toBe(false);
    expect(term!.clear).toHaveBeenCalled();
  });

  it('Ctrl-C / Ctrl-K, ⌘C without a selection, and plain keys reach the shell', async () => {
    await mount();
    const h = term!.keyHandler!;
    expect(h(key('c', { ctrlKey: true }))).toBe(true);
    expect(h(key('k', { ctrlKey: true }))).toBe(true);
    expect(h(key('c', { metaKey: true }))).toBe(true);   // nothing selected: let ⌘C through
    expect(h(key('x'))).toBe(true);
    expect(copyMock).not.toHaveBeenCalled();
  });

  it('⌘↵ adds the selection to the chat input with the cwd', async () => {
    await mount();
    const got: unknown[] = [];
    const on = (e: Event) => got.push((e as CustomEvent).detail);
    window.addEventListener('swarm:attach-terminal', on);
    term!.selection = 'picked\n';
    act(() => { expect(term!.keyHandler!(key('Enter', { metaKey: true }))).toBe(false); });
    window.removeEventListener('swarm:attach-terminal', on);
    expect(got).toEqual([{ bufferTail: 'picked', cwd: '/r/swarmai' }]);
  });

  it('⌘↵ with nothing selected adds the last lines and never reaches the shell', async () => {
    const t = await mount();
    t.getBuffer = () => 'tail lines';
    const got: unknown[] = [];
    const on = (e: Event) => got.push((e as CustomEvent).detail);
    window.addEventListener('swarm:attach-terminal', on);
    act(() => { expect(term!.keyHandler!(key('Enter', { metaKey: true }))).toBe(false); });
    window.removeEventListener('swarm:attach-terminal', on);
    expect(got).toEqual([{ bufferTail: 'tail lines', cwd: '/r/swarmai' }]);
  });

  it('right-click menu: Copy / Add to chat act on the selection, Add falls back to the last lines', async () => {
    const t = await mount();
    t.getBuffer = () => 'last lines';     // the mounted surface installed the real reader
    const surface = screen.getByTestId('terminal-surface-term-1');
    const got: unknown[] = [];
    const on = (e: Event) => got.push((e as CustomEvent).detail);
    window.addEventListener('swarm:attach-terminal', on);

    fireEvent.contextMenu(surface, { clientX: 10, clientY: 10 });
    expect(screen.getByTestId('terminal-menu')).toBeInTheDocument();
    expect(screen.getByTestId('terminal-menu-copy')).toBeDisabled();   // no selection
    fireEvent.mouseDown(screen.getByTestId('terminal-menu-add'));
    expect(screen.queryByTestId('terminal-menu')).toBeNull();

    term!.selection = 'sel';
    fireEvent.contextMenu(surface, { clientX: 10, clientY: 10 });
    fireEvent.mouseDown(screen.getByTestId('terminal-menu-copy'));
    expect(copyMock).toHaveBeenCalledWith('sel');
    fireEvent.contextMenu(surface, { clientX: 10, clientY: 10 });
    fireEvent.mouseDown(screen.getByTestId('terminal-menu-clear'));
    expect(term!.clear).toHaveBeenCalled();
    window.removeEventListener('swarm:attach-terminal', on);
    expect(got).toEqual([{ bufferTail: 'last lines', cwd: '/r/swarmai' }]);
  });

  it('the menu closes on Escape and on an outside mousedown', async () => {
    await mount();
    const surface = screen.getByTestId('terminal-surface-term-1');
    fireEvent.contextMenu(surface);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('terminal-menu')).toBeNull();
    fireEvent.contextMenu(surface);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('terminal-menu')).toBeNull();
  });
});
