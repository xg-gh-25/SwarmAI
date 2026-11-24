/**
 * TerminalTab's shortcuts per platform. On Windows and Linux the terminal's copy,
 * select-all, clear and add-to-chat keys are Ctrl+Shift+C / A / K / ↵, all kept from
 * the shell (Ctrl+Shift+C too when nothing is selected); plain Ctrl-C / Ctrl-K still
 * reach the shell. On macOS they stay ⌘C / ⌘A / ⌘K / ⌘↵ and Ctrl+Shift does nothing.
 * The labels in the add-to-chat button and the right-click menu follow the platform.
 *
 * The platform comes from navigator.platform (utils/platform detectOs), read once per
 * mount. xterm and the WebGL addon are mocked at the module boundary, as in
 * TerminalTab.selection.test.tsx; the component is real.
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
let platformSpy: { mockRestore: () => void } | null = null;
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


function onPlatform(platform: string) {
  platformSpy = vi.spyOn(navigator, 'platform', 'get').mockReturnValue(platform);
}

function attached(run: () => void): unknown[] {
  const got: unknown[] = [];
  const on = (e: Event) => got.push((e as CustomEvent).detail);
  window.addEventListener('swarm:attach-terminal', on);
  try { act(run); } finally { window.removeEventListener('swarm:attach-terminal', on); }
  return got;
}

function menuHints(): string[] {
  fireEvent.contextMenu(screen.getByTestId('terminal-surface-term-1'), { clientX: 10, clientY: 10 });
  const menu = screen.getByTestId('terminal-menu');
  return ['copy', 'add', 'select-all', 'clear'].map((k) =>
    menu.querySelector(`[data-testid="terminal-menu-${k}"] span:last-child`)!.textContent ?? '');
}

const shift = { ctrlKey: true, shiftKey: true };

/** Select *text* in the mocked xterm and fire its selection-change callback. */
function selectText(text: string) {
  const t = term as unknown as { selection: string; onSelectionChange: { mock: { calls: [() => void][] } } };
  t.selection = text;
  act(() => { t.onSelectionChange.mock.calls[0][0](); });
}

describe('TerminalTab shortcuts', () => {
  beforeEach(() => {
    term = null; lossCb = null; glDispose.mockClear(); copyMock.mockClear();
    vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); disconnect = vi.fn(); });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); platformSpy?.mockRestore(); platformSpy = null; });

  describe.each([['Linux', 'Linux x86_64'], ['Windows', 'Win32']])('%s', (_name, platform) => {
    beforeEach(() => onPlatform(platform));

    it('Ctrl+Shift+C copies the selection, Ctrl+Shift+A selects all, Ctrl+Shift+K clears, all kept from the shell', async () => {
      await mount();
      const h = term!.keyHandler!;
      term!.selection = 'FAIL x';
      expect(h(key('C', shift))).toBe(false);
      expect(copyMock).toHaveBeenCalledWith('FAIL x');
      expect(h(key('A', shift))).toBe(false);
      expect(term!.selectAll).toHaveBeenCalled();
      expect(h(key('K', shift))).toBe(false);
      expect(term!.clear).toHaveBeenCalled();
    });

    it('Ctrl+Shift+C with nothing selected is swallowed and copies nothing', async () => {
      await mount();
      expect(term!.keyHandler!(key('C', shift))).toBe(false);
      expect(copyMock).not.toHaveBeenCalled();
    });

    it('Ctrl+Shift+↵ adds the selection, else the last lines, to the chat input and never reaches the shell', async () => {
      const t = await mount();
      t.getBuffer = () => 'tail lines';
      term!.selection = 'picked\n';
      expect(attached(() => { expect(term!.keyHandler!(key('Enter', shift))).toBe(false); }))
        .toEqual([{ bufferTail: 'picked', cwd: '/r/swarmai' }]);
      term!.selection = '';
      expect(attached(() => { expect(term!.keyHandler!(key('Enter', shift))).toBe(false); }))
        .toEqual([{ bufferTail: 'tail lines', cwd: '/r/swarmai' }]);
    });

    it('plain Ctrl-C / Ctrl-K / Ctrl-A, the Windows/Super key and plain keys reach the shell', async () => {
      await mount();
      const h = term!.keyHandler!;
      term!.selection = 'sel';
      expect(h(key('c', { ctrlKey: true }))).toBe(true);
      expect(h(key('k', { ctrlKey: true }))).toBe(true);
      expect(h(key('a', { ctrlKey: true }))).toBe(true);
      expect(h(key('Enter', { ctrlKey: true }))).toBe(true);
      expect(h(key('c', { metaKey: true }))).toBe(true);
      expect(h(key('C', { ...shift, altKey: true }))).toBe(true);
      expect(h(key('x'))).toBe(true);
      expect(copyMock).not.toHaveBeenCalled();
      expect(term!.selectAll).not.toHaveBeenCalled();
      expect(term!.clear).not.toHaveBeenCalled();
    });

    it('labels say Ctrl+Shift in the menu and on the add-to-chat button', async () => {
      await mount();
      expect(menuHints()).toEqual(['Ctrl+Shift+C', 'Ctrl+Shift+↵', 'Ctrl+Shift+A', 'Ctrl+Shift+K']);
      fireEvent.keyDown(document, { key: 'Escape' });
      selectText('x');
      expect(screen.getByTestId('terminal-send-selection-term-1')).toHaveTextContent('Ctrl+Shift+↵');
      expect(screen.getByTestId('terminal-send-selection-term-1')).not.toHaveTextContent('⌘');
    });
  });

  describe('macOS', () => {
    beforeEach(() => onPlatform('MacIntel'));

    it('⌘ keys act, Ctrl+Shift reaches the shell, and the labels say ⌘', async () => {
      await mount();
      const h = term!.keyHandler!;
      term!.selection = 'sel';
      expect(h(key('c', { metaKey: true }))).toBe(false);
      expect(copyMock).toHaveBeenCalledWith('sel');
      expect(h(key('C', shift))).toBe(true);
      expect(h(key('K', shift))).toBe(true);
      expect(h(key('Enter', shift))).toBe(true);
      expect(term!.clear).not.toHaveBeenCalled();
      expect(menuHints()).toEqual(['⌘C', '⌘↵', '⌘A', '⌘K']);
      fireEvent.keyDown(document, { key: 'Escape' });
      selectText('sel');
      expect(screen.getByTestId('terminal-send-selection-term-1')).toHaveTextContent('⌘↵');
    });
  });
});
