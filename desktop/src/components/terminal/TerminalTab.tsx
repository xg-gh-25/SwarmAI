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
import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import type { TerminalTab as TerminalTabModel } from '../../stores/TerminalStore';

interface TerminalTabProps {
  tab: TerminalTabModel;
  /** Whether this tab is the visible/active one (drives fit on reveal). */
  active: boolean;
}

// A dark theme aligned with the app's chrome variables, WITH a full 16-color
// ANSI palette. The palette is what makes command output legible + distinct:
// once the shell has a color-capable $TERM (set in Rust pty_spawn), ls/git/errors
// emit ANSI color codes, and these are the colors xterm renders them in. Tuned
// for the #0a0d12 background (GitHub-dark-ish, high contrast, not muddy).
const XTERM_THEME = {
  background: '#0a0d12',
  foreground: '#e6edf3',
  cursor: '#58a6ff',
  cursorAccent: '#0a0d12',
  selectionBackground: '#2f5178',
  // Normal
  black: '#484f58',
  red: '#ff7b72',
  green: '#3fb950',
  yellow: '#d29922',
  blue: '#58a6ff',
  magenta: '#bc8cff',
  cyan: '#39c5cf',
  white: '#b1bac4',
  // Bright
  brightBlack: '#6e7681',
  brightRed: '#ffa198',
  brightGreen: '#56d364',
  brightYellow: '#e3b341',
  brightBlue: '#79c0ff',
  brightMagenta: '#d2a8ff',
  brightCyan: '#56d4dd',
  brightWhite: '#f0f6fc',
};

export default function TerminalTab({ tab, active }: TerminalTabProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
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
  // One-shot guard for the webfont cell re-measure. The cell width/height is
  // measured at open() with whatever font is resolved THEN — usually a fallback,
  // because JetBrains Mono is an async webfont. A wrong cell width drifts mouse
  // selection across the row. We force a re-measure once the font is loaded AND
  // the surface is visible+sized. Why BOTH triggers (fonts.ready AND reveal):
  // xterm's DomMeasureStrategy fallback reads offsetWidth, which is 0 while the
  // tab is display:none — so a fonts.ready that lands while this tab is inactive
  // no-ops on that path. Re-nudging on reveal (when the tab is sized) makes the
  // fix robust across both measure strategies (Gate-2 MEDIUM).
  const remeasuredRef = useRef(false);

  // Force xterm to re-measure the character cell with the currently-loaded font.
  // xterm's CharSizeService only re-measures on a CHANGED fontFamily value, so
  // nudge it (append a space → distinct value → change event, then restore →
  // second change event → measure runs). Idempotent via remeasuredRef.
  const remeasureCellOnce = (term: Terminal, fit: FitAddon, host: HTMLDivElement) => {
    if (remeasuredRef.current) return;
    const rect = host.getBoundingClientRect();
    // DOM measure strategy needs a laid-out (sized) surface; skip until visible.
    if (rect.width <= 0 || rect.height <= 0) return;
    const base = term.options.fontFamily ?? '';
    term.options.fontFamily = base + ' ';
    term.options.fontFamily = base;
    remeasuredRef.current = true;
    try {
      fit.fit();
      tab.pty.resize(term.cols, term.rows);
    } catch {
      /* fit can throw mid-layout; ignore */
    }
  };

  // Create xterm + wire to the (store-owned) PTY once per tab.id.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const term = new Terminal({
      cursorBlink: true,
      fontFamily:
        "'JetBrains Mono', 'SF Mono', Menlo, Monaco, 'Courier New', monospace",
      // Terminal is an auxiliary panel with little vertical space — pack it
      // densely. fontSize 11 + lineHeight 1.0 (was 12 / 1.3, which wasted ~30%
      // of every row to leading) fits noticeably more rows in the same height.
      fontSize: 11,
      lineHeight: 1.0,
      letterSpacing: 0,
      fontWeight: 400,
      fontWeightBold: 600,
      theme: XTERM_THEME,
      convertEol: false,
      scrollback: 10000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    fitRef.current = fit;

    // Fit only when the container actually has size (guards the 0-height race
    // when the panel is collapsed on first mount).
    const safeFit = () => {
      const rect = host.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        try {
          fit.fit();
          tab.pty.resize(term.cols, term.rows);
          // AC1: focus exactly once, when this tab is active AND now sized.
          // This is the recovery path for the first-mount height-0 race — the
          // [active] effect's own focus was skipped because the container had
          // no size yet. The focusedRef gate keeps it one-shot so subsequent
          // resize ticks never steal focus.
          if (activeRef.current && !focusedRef.current) {
            term.focus();
            focusedRef.current = true;
          }
        } catch {
          /* fit can throw mid-layout; ignore and retry on next resize */
        }
      }
    };
    safeFit();

    // PTY output → terminal (service already decodes Uint8Array→string, H2).
    const dataSub = tab.pty.onData((chunk) => term.write(chunk));
    // Terminal input (keystrokes) → PTY stdin (AC2 interactivity).
    const inputSub = term.onData((data) => tab.pty.write(data));

    // Expose the visible buffer text for the P2 "attach to chat" action.
    // Reads the last min(buffer.length, 200) lines of the active xterm buffer.
    tab.getBuffer = () => {
      const buf = term.buffer.active;
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
    // [active] effect below doesn't re-fire — without this the reopened terminal
    // is sized but not focused, and the user must click before typing).
    tab.focus = () => {
      if (termRef.current === term) term.focus();
    };

    // Re-fit when the container resizes (panel open/resize/window resize).
    const ro = new ResizeObserver(() => safeFit());
    ro.observe(host);

    // Re-measure the character cell once the terminal webfont has loaded (see
    // remeasureCellOnce above). This is the fonts.ready TRIGGER; the [active]
    // effect below is the reveal trigger. Whichever fires first with the surface
    // visible+sized wins (remeasuredRef makes it one-shot). Guarded on `disposed`
    // so a font resolving after unmount doesn't touch a disposed terminal.
    let disposed = false;
    if (typeof document !== 'undefined' && document.fonts?.ready) {
      document.fonts.ready
        .then(() => {
          if (disposed || termRef.current !== term) return;
          remeasureCellOnce(term, fit, host);
        })
        .catch(() => {
          /* fonts.ready never rejects in practice; ignore if it does */
        });
    }

    return () => {
      // M1: dispose all listeners + the xterm instance. The PTY is NOT killed
      // here — its lifecycle is owned by TerminalStore.closeTerminal, so a
      // StrictMode remount re-attaches to the same live shell (C1).
      disposed = true;
      ro.disconnect();
      dataSub.dispose();
      inputSub.dispose();
      term.dispose();
      tab.getBuffer = undefined;
      tab.focus = undefined;
      termRef.current = null;
      fitRef.current = null;
      // Re-arm the one-shot autofocus for a genuine remount (StrictMode
      // mount→cleanup→mount, or a real teardown). Reset lives ONLY here (the
      // creation effect, deps [tab.id,tab.pty]) — NOT in the [active] effect —
      // so switching tabs does not re-arm it; tab-switch refocus is owned by
      // the [active] effect's own term.focus() below.
      focusedRef.current = false;
      // Re-arm the one-shot cell re-measure for a genuine remount (a fresh xterm
      // instance must re-measure against the loaded font). Like focusedRef, this
      // reset lives ONLY in the creation effect ([tab.id,tab.pty]) — NOT in the
      // [active] effect — so a tab-SWITCH doesn't re-nudge fontFamily every time.
      remeasuredRef.current = false;
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
        // Reveal-time cell re-measure (one-shot). If the webfont loaded while
        // this tab was inactive (display:none → offsetWidth 0), the fonts.ready
        // trigger no-op'd on xterm's DOM measure strategy; now that the surface
        // is visible+sized, force the measure so selection maps correctly.
        remeasureCellOnce(term, fit, host);
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

  return (
    // Visibility is owned by the PARENT wrapper (TerminalPanel toggles the
    // `absolute inset-0` wrapper's display), which is the click-hit-test surface.
    // This inner host is always block inside its wrapper — a single source of
    // visibility truth (Gate-2: removed the redundant inner display toggle that
    // duplicated the wrapper's, which risked the two diverging).
    <div
      ref={hostRef}
      data-testid={`terminal-surface-${tab.id}`}
      className="h-full w-full overflow-hidden pl-2 pr-1 py-0.5"
    />
  );
}
