/**
 * TerminalTab — one xterm.js Terminal instance wired to one PTY (IPty).
 *
 * Renders the actual terminal surface for a single tab. Mounted per tab but
 * only the ACTIVE tab is visible (display toggled by the parent) so background
 * terminals keep streaming without being torn down.
 *
 * Gate-1 findings applied here (this is the highest-risk component):
 *   - C1 (StrictMode double-mount): the xterm Terminal + fit addon + IPty
 *     listeners are created ONCE and disposed in the effect cleanup. The PTY
 *     itself is owned by TerminalStore (not spawned here), so a dev double-mount
 *     re-attaches xterm to the SAME pty rather than spawning a second shell.
 *   - H1 (invisible terminal): '@xterm/xterm/css/xterm.css' is imported.
 *   - M1 (listener leak): pty.onData / term.onData disposables are cleaned up.
 *   - H5-adjacent (fit on 0-height): fit() is guarded on a >0 sized container
 *     and re-run when the panel becomes visible (ResizeObserver).
 */
import { useEffect, useRef, useState } from 'react';
import { Terminal, type ITheme } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { sendTerminalTextToChat, type TerminalTab as TerminalTabModel } from '../../stores/TerminalStore';
import { copyToClipboard } from '../../utils/clipboard';

interface TerminalTabProps {
  tab: TerminalTabModel;
  /** Whether this tab is the visible/active one (drives fit on reveal). */
  active: boolean;
}

// ANSI palettes per app theme. Background / foreground / cursor come from the app's
// own CSS variables at runtime (themeFor), so the terminal surface is the app surface;
// the 16 ANSI colours are tuned per theme for contrast on it (GitHub dark / light).
// Once the shell has a colour-capable $TERM (Rust pty_spawn), ls/git/errors emit
// these codes.
const DARK_ANSI = {
  black: '#484f58', red: '#ff7b72', green: '#3fb950', yellow: '#d29922',
  blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#b1bac4',
  brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364', brightYellow: '#e3b341',
  brightBlue: '#79c0ff', brightMagenta: '#d2a8ff', brightCyan: '#56d4dd', brightWhite: '#f0f6fc',
};
const LIGHT_ANSI = {
  black: '#24292f', red: '#cf222e', green: '#116329', yellow: '#7d4e00',
  blue: '#0969da', magenta: '#8250df', cyan: '#1b7c83', white: '#6e7781',
  brightBlack: '#57606a', brightRed: '#a40e26', brightGreen: '#1a7f37', brightYellow: '#633c01',
  brightBlue: '#218bff', brightMagenta: '#a475f9', brightCyan: '#3192aa', brightWhite: '#8c959f',
};

function isDark(): boolean {
  return typeof document === 'undefined' || !document.documentElement.classList.contains('light');
}

/**
 * Render with WebGL (GPU) instead of the DOM renderer — the DOM renderer is the
 * slow path for a fast log or a long scroll. Loaded lazily (its own chunk) after the
 * terminal opens. No WebGL, a failed init, or a lost context (the webview caps live
 * GL contexts, so many terminals can lose the oldest) disposes the addon, and xterm
 * goes back to the DOM renderer on its own. *gone* says the terminal was disposed
 * while the chunk loaded.
 */
function loadWebgl(t: Terminal, gone: () => boolean): void {
  void import('@xterm/addon-webgl')
    .then(({ WebglAddon }) => {
      if (gone()) return;
      const gl = new WebglAddon();
      gl.onContextLoss(() => gl.dispose());
      t.loadAddon(gl);
    })
    .catch((e) => console.info('terminal: WebGL renderer unavailable, using DOM', e));
}

/** The xterm theme for the current app theme, its surface read from CSS variables. */
function themeFor(dark: boolean): ITheme {
  const css = typeof document === 'undefined' ? null : getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  const background = v('--color-bg', dark ? '#111318' : '#f8fafc');
  return {
    background,
    foreground: v('--color-text', dark ? '#e6edf3' : '#1f2328'),
    cursor: v('--color-primary', dark ? '#58a6ff' : '#0969da'),
    cursorAccent: background,
    selectionBackground: dark ? '#2f5178' : '#b6d3f5',
    ...(dark ? DARK_ANSI : LIGHT_ANSI),
  };
}

/** Follows the app theme (ThemeContext puts 'light' / 'dark' on <html>, the accent in
 *  data-accent) without a provider dependency — the panel renders in tests without
 *  ThemeProvider. Returns a key that changes with either. */
function useThemeKey(): string {
  const read = () =>
    `${isDark() ? 'dark' : 'light'}|${typeof document === 'undefined' ? '' : document.documentElement.dataset.accent ?? ''}`;
  const [key, setKey] = useState(read);
  useEffect(() => {
    const mo = new MutationObserver(() => setKey(read()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-accent'] });
    return () => mo.disconnect();
  }, []);
  return key;
}

export default function TerminalTab({ tab, active }: TerminalTabProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const termRef = useRef<Terminal | null>(null);
  // Mirror of the `active` prop readable from inside the creation effect's
  // safeFit closure WITHOUT re-running that effect (its deps are [tab.id,
  // tab.pty]). Updated every render below.
  const activeRef = useRef(active);
  activeRef.current = active;
  // One-shot autofocus guard (AC1). term.focus() must fire exactly once when
  // the surface first becomes active AND sized — the [active] effect alone
  // misses it because on first panel-open the tab mounts active=true in the
  // SAME commit the flex panel mounts, so getBoundingClientRect() is height 0
  // and the height-gated focus is skipped; the ResizeObserver/safeFit settle
  // path must then focus. Gated so safeFit (which runs on EVERY resize tick)
  // does NOT re-focus and steal focus from chat on later resizes.
  const focusedRef = useRef(false);
  const themeKey = useThemeKey();
  // Whether the user has text selected (drives the "Add to chat" pill), the pill's
  // brief "Added" acknowledgement, and the right-click menu's position.
  const [hasSelection, setHasSelection] = useState(false);
  const [sent, setSent] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const sendSelection = (t: Terminal | null): boolean => {
    const text = t?.getSelection() ?? '';
    if (!sendTerminalTextToChat(text.replace(/\s+$/, ''), tab.cwd)) return false;
    setSent(true);
    window.setTimeout(() => setSent(false), 1500);
    return true;
  };
  const sendSelectionRef = useRef(sendSelection);
  sendSelectionRef.current = sendSelection;

  // Create xterm + wire to the (store-owned) PTY once per tab.id.
  //
  // FONT IS PRELOADED BEFORE THE TERMINAL IS CONSTRUCTED — this is the fix for
  // mouse-selection drift. xterm measures the character cell exactly once, at
  // construction, with whatever font is resolved at that instant; selection
  // hit-testing then maps mouse-x → column by dividing by that measured cell
  // width. JetBrains Mono is an async webfont (@fontsource, main.tsx), so
  // constructing xterm before it loads measures a FALLBACK (Menlo) cell.
  // Measured from the running app (diagnostic probe, run_6a148449): xterm's
  // charSize width was 6.6226px (Menlo) while glyphs render at 6.4688px (JBM) —
  // a 0.153px/column error that accumulates across the row (col 100 ≈ 15px ≈
  // 2.4 chars off), the exact "越往右越不准" the user saw. Awaiting
  // document.fonts.load() first makes that single measurement use JBM → grid
  // matches glyphs, zero drift, no re-measure race. (This REPLACES a prior
  // fontFamily-nudge re-measure whose probe proved it never corrected the
  // metric — charSize stayed 6.62 even after fonts.ready fired.)
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    // These are captured for cleanup; they stay null if the effect is torn down
    // while the font preload is still awaiting (StrictMode / fast tab close).
    let disposed = false;
    let term: Terminal | null = null;
    let ro: ResizeObserver | null = null;
    let dataSub: { dispose: () => void } | null = null;
    let inputSub: { dispose: () => void } | null = null;
    let selSub: { dispose: () => void } | null = null;

    const init = async () => {
      // Preload the exact faces xterm will measure (regular 400 + bold 600) so
      // its one-shot cell measurement uses JetBrains Mono, not a fallback.
      // Best-effort: if the API is missing or rejects, fall through and build
      // anyway (worst case = the pre-fix fallback metric — never block the
      // terminal on a font).
      try {
        const fonts = (document as unknown as { fonts?: FontFaceSet }).fonts;
        if (fonts?.load) {
          // Preload ONLY the regular (400) face — that is the one xterm measures
          // for its cell metric (the drift fix). Do NOT wait on 600: main.tsx
          // imports only the 400/500 @fontsource faces, so a 600 request just
          // weight-selects the 400 face (resolves, but waits on nothing real);
          // bold text synth-bolds at render regardless. (Gate-2 MEDIUM.)
          await fonts.load("11px 'JetBrains Mono'");
        }
      } catch {
        /* font preload is best-effort; construct with fallback if it fails */
      }
      // The effect may have been cleaned up while we awaited the font.
      if (disposed || !hostRef.current) return;

      const t = new Terminal({
        cursorBlink: true,
        fontFamily:
          "'SF Mono', Menlo, 'JetBrains Mono', Monaco, 'Courier New', monospace",
        // Terminal is an auxiliary panel with little vertical space — pack it
        // densely. fontSize 11 + lineHeight 1.0 (was 12 / 1.3, which wasted ~30%
        // of every row to leading) fits noticeably more rows in the same height.
        fontSize: 11,
        lineHeight: 1.0,
        letterSpacing: 0,
        fontWeight: 400,
        fontWeightBold: 600,
        theme: themeFor(isDark()),
        convertEol: false,
        scrollback: 10000,
        // A program that enables mouse reporting (vim, less, htop, a TUI) takes
        // every drag; ⌥-drag still selects text (the macOS Terminal / iTerm habit).
        macOptionClickForcesSelection: true,
      });
      const f = new FitAddon();
      t.loadAddon(f);
      t.open(host);
      term = t;
      termRef.current = t;
      fitRef.current = f;
      loadWebgl(t, () => disposed);

      // ⌘C copies the selection, ⌘A selects all, ⌘K clears the screen + scrollback,
      // ⌘↵ adds the selection to the chat input. All swallowed (never sent to the
      // shell). macOS ⌘ only — Ctrl-C / Ctrl-K must reach the shell. ⌘C goes
      // through the app's native clipboard command: navigator.clipboard is denied
      // in the Tauri webview (utils/clipboard). ⌘V is left to the native paste.
      t.attachCustomKeyEventHandler((e) => {
        if (e.type !== 'keydown' || !e.metaKey || e.ctrlKey || e.altKey) return true;
        const k = e.key.toLowerCase();
        if (k === 'c' && t.hasSelection()) {
          void copyToClipboard(t.getSelection());
          return false;
        }
        if (k === 'a') {
          t.selectAll();
          return false;
        }
        if (k === 'k') {
          t.clear();
          return false;
        }
        if (e.key === 'Enter') {
          // Never reaches the shell (⌘↵ would run the typed command): the
          // selection, else the last lines, goes to the chat input.
          if (t.hasSelection()) sendSelectionRef.current(t);
          else if (tab.getBuffer) sendTerminalTextToChat(tab.getBuffer(), tab.cwd);
          return false;
        }
        return true;
      });
      selSub = t.onSelectionChange(() => setHasSelection(t.hasSelection()));

      // Fit only when the container actually has size (guards the 0-height race
      // when the panel is collapsed on first mount).
      const safeFit = () => {
        const rect = host.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          try {
            f.fit();
            tab.pty.resize(t.cols, t.rows);
            // AC1: focus exactly once, when this tab is active AND now sized.
            // Recovery path for the first-mount height-0 race — the [active]
            // effect's own focus was skipped because the container had no size
            // yet. The focusedRef gate keeps it one-shot so later resize ticks
            // never steal focus.
            if (activeRef.current && !focusedRef.current) {
              t.focus();
              focusedRef.current = true;
            }
          } catch {
            /* fit can throw mid-layout; ignore and retry on next resize */
          }
        }
      };
      safeFit();

      // PTY output → terminal (service already decodes Uint8Array→string, H2).
      dataSub = tab.pty.onData((chunk) => t.write(chunk));
      // Terminal input (keystrokes) → PTY stdin (AC2 interactivity).
      inputSub = t.onData((data) => tab.pty.write(data));

      // Expose the visible buffer text for the P2 "attach to chat" action.
      // Reads the last min(buffer.length, 200) lines of the active xterm buffer.
      tab.getBuffer = () => {
        const buf = t.buffer.active;
        const lines: string[] = [];
        const maxLines = Math.min(buf.length, 200);
        const start = buf.length - maxLines;
        for (let i = start; i < buf.length; i++) {
          const line = buf.getLine(i);
          if (line) lines.push(line.translateToString(true));
        }
        return lines.join('\n').replace(/\n+$/, '');
      };

      // Expose focus() so TerminalPanel can focus the active terminal when the
      // panel is REVEALED (collapse→reopen keeps `active` unchanged, so the
      // [active] effect below doesn't re-fire — without this the reopened
      // terminal is sized but not focused, and the user must click before typing).
      tab.focus = () => {
        if (termRef.current === t) t.focus();
      };
      tab.getSelection = () => (termRef.current === t ? t.getSelection() : '');

      // Re-fit when the container resizes (panel open/resize/window resize).
      ro = new ResizeObserver(() => safeFit());
      ro.observe(host);
    };
    void init();

    return () => {
      // M1: dispose all listeners + the xterm instance. The PTY is NOT killed
      // here — its lifecycle is owned by TerminalStore.closeTerminal, so a
      // StrictMode remount re-attaches to the same live shell (C1). Everything
      // is null-guarded: cleanup can run before init() finished awaiting fonts.
      disposed = true;
      ro?.disconnect();
      dataSub?.dispose();
      inputSub?.dispose();
      selSub?.dispose();
      term?.dispose();
      tab.getBuffer = undefined;
      tab.focus = undefined;
      tab.getSelection = undefined;
      termRef.current = null;
      fitRef.current = null;
      // Re-arm the one-shot autofocus for a genuine remount (StrictMode
      // mount→cleanup→mount, or a real teardown). Reset lives ONLY here (the
      // creation effect, deps [tab.id,tab.pty]) — NOT in the [active] effect —
      // so switching tabs does not re-arm it; tab-switch refocus is owned by
      // the [active] effect's own term.focus() below.
      focusedRef.current = false;
    };
  }, [tab.id, tab.pty]);

  // When this tab becomes active (revealed), fit + focus the now-sized container.
  useEffect(() => {
    if (!active) return;
    const host = hostRef.current;
    const fit = fitRef.current;
    const term = termRef.current;
    if (!host || !fit || !term) return;
    const rect = host.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      try {
        // The cell metric is already correct — the font is preloaded before the
        // terminal is constructed (see creation effect), so no reveal-time
        // re-measure is needed. Just refit the now-sized container + focus.
        fit.fit();
        tab.pty.resize(term.cols, term.rows);
        // Focus on tab-switch reveal. Mark focused so the safeFit one-shot
        // (which shares focusedRef) doesn't then double-focus on the resize
        // tick this fit triggers.
        term.focus();
        focusedRef.current = true;
      } catch {
        /* ignore */
      }
    } else {
      // Not laid out yet (display:none→block on switch-back, or first mount at
      // height 0). Re-arm the one-shot so the ResizeObserver/safeFit settle path
      // focuses on the next tick — otherwise a switch-back that reads height 0
      // here would leave the tab unfocused with no recovery (focusedRef stuck
      // true from the prior activation). (Adversarial: MED switch-back gap.)
      focusedRef.current = false;
    }
  }, [active, tab.pty]);

  // Re-theme a live terminal when the app theme / accent changes.
  useEffect(() => {
    const term = termRef.current;
    if (term) term.options.theme = themeFor(themeKey.startsWith('dark'));
  }, [themeKey]);

  return (
    // Visibility is owned by the PARENT wrapper (TerminalPanel toggles the
    // `absolute inset-0` wrapper's display), which is the click-hit-test surface.
    // This inner host is always block inside its wrapper — a single source of
    // visibility truth (Gate-2: removed the redundant inner display toggle that
    // duplicated the wrapper's, which risked the two diverging).
    <div
      ref={wrapRef}
      className="relative h-full w-full"
      // Right-click: our menu instead of the webview's. macOS right-click already
      // selects the word under the pointer (xterm rightClickSelectsWord), so
      // "Add to chat" acts on it. Positions are wrapper-relative; the wrapper's
      // net scale is 1 (TerminalPanel's counter-zoom), so client px are CSS px.
      onContextMenu={(e) => {
        e.preventDefault();
        const r = wrapRef.current?.getBoundingClientRect();
        if (!r) return;
        // Read the selection NOW: macOS right-click just selected the word under the
        // pointer, and onSelectionChange may not have re-rendered yet.
        setHasSelection(!!termRef.current?.hasSelection());
        setMenu({
          x: Math.min(e.clientX - r.left, Math.max(0, r.width - 196)),
          y: Math.min(e.clientY - r.top, Math.max(0, r.height - 170)),
        });
      }}
    >
      <div
        ref={hostRef}
        data-testid={`terminal-surface-${tab.id}`}
        className="h-full w-full overflow-hidden pl-2 pr-1 py-0.5"
      />
      {(hasSelection || sent) && !menu && (
        <button
          type="button"
          data-testid={`terminal-send-selection-${tab.id}`}
          // mousedown, not click: keep the xterm selection (a click would clear it).
          onMouseDown={(e) => {
            e.preventDefault();
            sendSelection(termRef.current);
          }}
          className="btn-sm btn-secondary absolute top-1.5 right-3 shadow-sm bg-[var(--color-bg-chrome)]"
        >
          <span className="material-symbols-outlined icon-xs">{sent ? 'check' : 'add_comment'}</span>
          {sent ? 'Added to chat' : 'Add to chat'}
          {!sent && <span className="text-[var(--color-text-muted)] font-normal">⌘↵</span>}
        </button>
      )}
      {menu && (
        <TerminalMenu
          x={menu.x}
          y={menu.y}
          hasSelection={hasSelection}
          onClose={() => setMenu(null)}
          onCopy={() => { const t = termRef.current; if (t?.hasSelection()) void copyToClipboard(t.getSelection()); }}
          onAdd={() => {
            const t = termRef.current;
            if (t?.hasSelection()) sendSelection(t);
            else if (tab.getBuffer) sendTerminalTextToChat(tab.getBuffer(), tab.cwd);
          }}
          onSelectAll={() => termRef.current?.selectAll()}
          onClear={() => termRef.current?.clear()}
        />
      )}
    </div>
  );
}

/** The terminal's right-click menu. Items act on mousedown so the xterm selection
 *  is still there when they run; it closes on outside mousedown, Escape or scroll. */
function TerminalMenu({ x, y, hasSelection, onClose, onCopy, onAdd, onSelectAll, onClear }: {
  x: number; y: number; hasSelection: boolean; onClose: () => void;
  onCopy: () => void; onAdd: () => void; onSelectAll: () => void; onClear: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('wheel', onClose, { passive: true });
    window.addEventListener('blur', onClose);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
      window.removeEventListener('wheel', onClose);
      window.removeEventListener('blur', onClose);
    };
  }, [onClose]);
  const item = (key: string, icon: string, label: string, hint: string, run: () => void, disabled = false) => (
    <button
      key={key}
      type="button"
      role="menuitem"
      data-testid={`terminal-menu-${key}`}
      disabled={disabled}
      onMouseDown={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (disabled) return;
        run();
        onClose();
      }}
      className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[12px] text-[var(--color-text)] hover:bg-[var(--color-hover)] disabled:opacity-40 disabled:hover:bg-transparent"
    >
      <span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">{icon}</span>
      <span className="flex-1">{label}</span>
      <span className="text-[10.5px] text-[var(--color-text-muted)]">{hint}</span>
    </button>
  );
  return (
    <div
      ref={ref}
      role="menu"
      data-testid="terminal-menu"
      style={{ left: x, top: y }}
      className="absolute z-50 w-48 p-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-lg"
    >
      {item('copy', 'content_copy', 'Copy', '⌘C', onCopy, !hasSelection)}
      {item('add', 'add_comment', hasSelection ? 'Add selection to chat' : 'Add last 200 lines to chat', '⌘↵', onAdd)}
      <div className="h-px my-1 bg-[var(--color-border)]" />
      {item('select-all', 'select_all', 'Select all', '⌘A', onSelectAll)}
      {item('clear', 'mop', 'Clear', '⌘K', onClear)}
    </div>
  );
}
