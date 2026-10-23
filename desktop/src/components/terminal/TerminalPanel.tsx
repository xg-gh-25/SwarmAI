/**
 * TerminalPanel — the bottom terminal panel (docs/design/terminal-locations.md).
 *
 * Header: a location switcher (SwarmWS · this chat's working repo · the chosen or
 * still-running custom folders, each with a shell-count badge), `⋯` (recent folders +
 * Browse…), then the shells AT the selected location, `＋`, send-to-chat and hide.
 * Body: a stack of TerminalTab surfaces (all mounted so background shells keep
 * streaming; only the shown one is visible), an exited-shell bar, or an empty state.
 *
 * Location rules: clicking a location selects it for this chat tab and shows its
 * shells, opening one if it has none; switching chat tabs only SHOWS (never spawns);
 * revealing the panel opens a shell at the location when it has none.
 *
 * Layout (Gate-1 C3): a fixed-height flex sibling BELOW the chat row and ABOVE the
 * BottomBar, resizable from its top edge. It never overlays chat. It is always
 * mounted and hidden with display:none when collapsed, so scrollback + PTYs survive.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useTerminal, TERMINAL_GOTO_EVENT } from '../../contexts/TerminalContext';
import {
  terminalStore,
  sendTerminalTextToChat,
  baseName,
  keyOfCwd,
  SWARMWS_KEY,
  type TerminalTab as TerminalTabModel,
} from '../../stores/TerminalStore';
import { terminalLocation, type LocationChoice } from '../../stores/terminalLocation';
import TerminalTab from './TerminalTab';

const MIN_HEIGHT = 120;
const MAX_HEIGHT = 640;
const DEFAULT_HEIGHT = 280;

interface Segment {
  key: string;
  kind: 'swarmws' | 'repo' | 'custom';
  label: string;
  cwd?: string;
  title: string;
}

const SEG_ICON: Record<Segment['kind'], string> = { swarmws: 'hive', repo: 'code', custom: 'folder' };
const SEG_ICON_COLOR: Record<Segment['kind'], string> = {
  swarmws: 'text-amber-500',
  repo: 'text-[var(--color-primary)]',
  custom: 'text-[var(--color-text-muted)]',
};

function parentName(path: string): string {
  const parts = path.replace(/\/+$/, '').split('/');
  return parts.length > 1 ? parts[parts.length - 2] : '';
}

/** The switcher's segments: SwarmWS, the working repo, the selected custom folder and
 *  every other location that still has shells (no running shell is ever hidden). Two
 *  segments with one name get their parent folder appended. */
function buildSegments(repo: { path: string; name: string } | null, current: { kind: string; key: string },
                       tabs: TerminalTabModel[]): Segment[] {
  const segs: Segment[] = [{ key: SWARMWS_KEY, kind: 'swarmws', label: 'SwarmWS', title: 'SwarmWS (~/.swarm-ai/SwarmWS)' }];
  if (repo) {
    segs.push({ key: keyOfCwd(repo.path), kind: 'repo', label: repo.name, cwd: repo.path,
                title: `${repo.path} — this chat's working repo` });
  }
  const add = (key: string) => {
    if (segs.some((s) => s.key === key)) return;
    segs.push({ key, kind: 'custom', label: baseName(key), cwd: key, title: key });
  };
  if (current.kind === 'custom') add(current.key);
  for (const t of tabs) if (t.locKey !== SWARMWS_KEY) add(t.locKey);
  for (const s of segs) {
    if (s.cwd && segs.some((o) => o !== s && o.label === s.label)) {
      const parent = parentName(s.cwd);
      if (parent) s.label = `${s.label} · ${parent}`;
    }
  }
  return segs;
}

function choiceOf(seg: Segment): LocationChoice {
  if (seg.kind === 'swarmws') return { kind: 'swarmws' };
  if (seg.kind === 'repo') return { kind: 'repo' };
  return { kind: 'custom', path: seg.cwd ?? seg.key };
}

/** Open the native folder picker; the chosen folder, or null when cancelled. */
async function browseFolder(): Promise<string | null> {
  try {
    const { open } = await import('@tauri-apps/plugin-dialog');
    const picked = await open({ directory: true, multiple: false, title: 'Open terminal in folder' });
    return typeof picked === 'string' && picked ? picked : null;
  } catch (e) {
    console.error('terminal: folder dialog failed', e);
    return null;
  }
}

/** The chevron next to the folder button: recent folders (global, newest first). */
function RecentsMenu({ recents, onPick, onClose }: {
  recents: string[]; onPick: (path: string) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [onClose]);
  return (
    <div
      ref={ref}
      data-testid="terminal-recents-menu"
      className="absolute left-0 top-full mt-1.5 z-50 w-72 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-lg p-1.5 text-[12.5px]"
    >
      <div className="px-2 pt-0.5 pb-1 text-[11px] text-[var(--color-text-muted)]">Recent folders</div>
      {recents.map((p) => (
        <button
          key={p}
          type="button"
          data-testid="terminal-recent"
          title={p}
          onClick={() => { onPick(p); onClose(); }}
          className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-[var(--color-text)] hover:bg-[var(--color-hover)]"
        >
          <span className="material-symbols-outlined icon-sm text-[var(--color-text-muted)]">folder</span>
          <span className="truncate">{baseName(p)}</span>
          <span className="ml-auto truncate max-w-[45%] text-[11px] text-[var(--color-text-muted)]">{parentName(p)}</span>
        </button>
      ))}
    </div>
  );
}

const ICON_BTN =
  'icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] disabled:opacity-40 disabled:pointer-events-none';

export default function TerminalPanel() {
  const {
    tabs,
    activeTabId,
    panelOpen,
    openTerminal,
    closeTerminal,
    restartTerminal,
    setActiveTab,
    setPanelOpen,
  } = useTerminal();
  const loc = useSyncExternalStore(
    (cb) => terminalLocation.subscribe(cb),
    () => terminalLocation.snapshot(),
  );
  const current = loc.current;

  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [sentFrom, setSentFrom] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const dragState = useRef<{ startY: number; startH: number } | null>(null);
  // The shell last shown at each location, so coming back shows the same one.
  const lastShown = useRef(new Map<string, string>());
  const panelOpenRef = useRef(panelOpen);
  panelOpenRef.current = panelOpen;

  const segments = useMemo(() => buildSegments(loc.workingRepo, current, tabs), [loc.workingRepo, current, tabs]);
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const t of tabs) m.set(t.locKey, (m.get(t.locKey) ?? 0) + 1);
    return m;
  }, [tabs]);
  const here = useMemo(() => tabs.filter((t) => t.locKey === current.key), [tabs, current.key]);
  const shown = here.find((t) => t.id === activeTabId) ?? null;
  const currentSeg = segments.find((s) => s.key === current.key) ?? segments[0];
  // Recent folders not already a segment (those are one click away already).
  const recents = useMemo(
    () => loc.recents.filter((r) => !segments.some((s) => s.key === r)),
    [loc.recents, segments],
  );

  useEffect(() => {
    if (shown) lastShown.current.set(shown.locKey, shown.id);
  }, [shown]);

  /** Show location *key*: its last-shown (else newest) shell, or open one at *cwd*. */
  const goTo = useCallback((key: string, cwd?: string) => {
    const at = terminalStore.listAt(key);
    if (at.length === 0) {
      openTerminal({ cwd });
      return;
    }
    const remembered = lastShown.current.get(key);
    setActiveTab(at.find((t) => t.id === remembered)?.id ?? at[at.length - 1].id);
    setPanelOpen(true);
  }, [openTerminal, setActiveTab, setPanelOpen]);

  // The location changed (chat tab switch, a choice, the working repo arriving):
  // SHOW its shell when it has one. Never spawns — a chat-tab switch must not open
  // shells; the empty state offers one instead.
  useEffect(() => {
    const at = terminalStore.listAt(current.key);
    if (at.length === 0 || at.some((t) => t.id === activeTabId)) return;
    const remembered = lastShown.current.get(current.key);
    setActiveTab(at.find((t) => t.id === remembered)?.id ?? at[at.length - 1].id);
    // activeTabId is deliberately not a dependency: closing the shown shell must
    // not jump here (closeTerminal re-points it).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.key, setActiveTab]);

  // Panel REVEALED: read the working repo FRESH (a run may have changed it since the
  // last read), then open a shell at the location if it has none — but only if the
  // panel is still open and the same chat is still in front (generation), so a slow
  // answer never opens a shell for another chat. Guarded on terminalStore.countAt()
  // — the LIVE registry — so StrictMode's double effect opens exactly one. Gated on
  // panelOpen, not mount: the panel is always mounted, and a mount-time spawn would
  // start a shell at app startup.
  useEffect(() => {
    if (!panelOpen) return;
    const gen = terminalLocation.generation();
    void terminalLocation.refresh().then(() => {
      if (!panelOpenRef.current || terminalLocation.generation() !== gen) return;
      const cur = terminalLocation.snapshot().current;
      if (terminalStore.countAt(cur.key) === 0) openTerminal({ cwd: cur.cwd });
    });
    // openTerminal is stable (useCallback); re-run only when panelOpen flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelOpen]);

  // BottomBar location chip / ⌘⇧`: reveal at the current location.
  useEffect(() => {
    const onGoto = () => {
      const cur = terminalLocation.snapshot().current;
      goTo(cur.key, cur.cwd);
    };
    window.addEventListener(TERMINAL_GOTO_EVENT, onGoto);
    return () => window.removeEventListener(TERMINAL_GOTO_EVENT, onGoto);
  }, [goTo]);

  // Focus the shown terminal when the panel is REVEALED (collapse→reopen keeps the
  // tab's `active` prop unchanged, so TerminalTab's own focus effect doesn't
  // re-fire). rAF waits for the display:none→flex layout.
  useEffect(() => {
    if (!panelOpen || !shown) return;
    const id = shown.id;
    const raf = requestAnimationFrame(() => terminalStore.get(id)?.focus?.());
    return () => cancelAnimationFrame(raf);
  }, [panelOpen, shown]);

  const selectSegment = (seg: Segment) => {
    terminalLocation.choose(choiceOf(seg));
    goTo(seg.key, seg.cwd);
  };

  /** Close a shell; when it was the shown one, show the newest remaining shell at
   *  the same location (the context alone re-points to the newest shell anywhere). */
  const closeHere = (id: string) => {
    const wasShown = id === shown?.id;
    const key = terminalStore.get(id)?.locKey;
    closeTerminal(id);
    if (wasShown && key) {
      const rest = terminalStore.listAt(key);
      if (rest.length > 0) setActiveTab(rest[rest.length - 1].id);
    }
  };

  const pickFolder = (path: string) => {
    const key = keyOfCwd(path);
    terminalLocation.choose({ kind: 'custom', path: key });
    goTo(key, key);
  };

  const sendToChat = () => {
    if (!shown) return;
    const text = shown.getSelection?.().replace(/\s+$/, '') || shown.getBuffer?.() || '';
    if (!sendTerminalTextToChat(text, shown.cwd)) return;
    const id = shown.id;
    setSentFrom(id);
    window.setTimeout(() => setSentFrom((cur) => (cur === id ? null : cur)), 2000);
  };

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      dragState.current = { startY: e.clientY, startH: height };
      const onMove = (ev: MouseEvent) => {
        if (!dragState.current) return;
        // Drag up = taller (panel grows upward from the bottom).
        const delta = dragState.current.startY - ev.clientY;
        setHeight(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, dragState.current.startH + delta)));
      };
      const onUp = () => {
        dragState.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [height],
  );

  return (
    <div
      data-testid="terminal-panel"
      // Two layers (docs/design/terminal-locations.md "Layout"): a recessed chrome
      // gutter that the resize handle lives in, and the terminal as a raised card
      // inset in it — the same depth Canvas uses, so the terminal reads as its own
      // surface, not as more chat.
      className="flex flex-col flex-shrink-0 bg-[var(--color-bg-chrome)] border-t border-[var(--color-border)] px-2.5 pb-2.5"
      // display:none when collapsed (NOT unmount): xterm scrollback + live PTYs
      // survive collapse/reopen, and the element leaves the flow so chat reclaims
      // the full height.
      style={{ height, display: panelOpen ? 'flex' : 'none' }}
    >
      {/* drag-to-resize handle: the gutter above the card, a grip on hover */}
      <div
        data-testid="terminal-resize-handle"
        onMouseDown={onDragStart}
        className="group h-2.5 w-full flex-shrink-0 cursor-ns-resize flex items-center justify-center"
      >
        <span className="h-[3px] w-10 rounded-full bg-[var(--color-border)] opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>

      <div className="terminal-card flex flex-col flex-1 min-h-0">
      {/* header */}
      <div className="flex items-center h-10 gap-1.5 px-2.5 border-b border-[var(--color-border)] text-[12px] flex-shrink-0">
        <div className="relative flex items-center">
          <div
            role="tablist"
            aria-label="Terminal location"
            data-testid="terminal-locations"
            className="flex items-center gap-0.5 p-[3px] rounded-lg bg-[color-mix(in_srgb,var(--color-bg)_45%,var(--color-card))]"
          >
            {segments.map((seg) => {
              const on = seg.key === current.key;
              const n = counts.get(seg.key) ?? 0;
              return (
                <button
                  key={seg.key}
                  type="button"
                  role="tab"
                  aria-selected={on}
                  title={seg.title}
                  data-testid={`terminal-loc-${seg.kind}`}
                  data-key={seg.key}
                  onClick={() => selectSegment(seg)}
                  className={`flex items-center gap-1.5 h-[26px] px-2.5 rounded-md max-w-[200px] transition-colors ${
                    on
                      ? 'bg-[var(--color-hover)] text-[var(--color-text)]'
                      : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                  }`}
                >
                  <span className={`material-symbols-outlined icon-xs ${SEG_ICON_COLOR[seg.kind]}`}>{SEG_ICON[seg.kind]}</span>
                  <span className="truncate">{seg.label}</span>
                  {!on && n > 0 && (
                    <span
                      data-testid={`terminal-loc-count-${seg.kind}`}
                      className="min-w-[14px] px-1 rounded-full text-[9.5px] leading-[14px] text-center bg-[var(--color-border)] text-[var(--color-text-muted)]"
                    >
                      {n}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            data-testid="terminal-browse"
            aria-label="Open a terminal in a folder"
            title="Open a terminal in a folder…"
            onClick={() => void browseFolder().then((p) => { if (p) pickFolder(p); })}
            className={`${ICON_BTN} ml-1`}
          >
            <span className="material-symbols-outlined icon-sm">create_new_folder</span>
          </button>
          {recents.length > 0 && (
            <button
              type="button"
              data-testid="terminal-more-locations"
              aria-label="Recent folders"
              title="Recent folders"
              onClick={() => setMenuOpen((v) => !v)}
              className={`${ICON_BTN} !w-5 -ml-0.5`}
            >
              <span className="material-symbols-outlined icon-sm">expand_more</span>
            </button>
          )}
          {menuOpen && recents.length > 0 && (
            <RecentsMenu
              recents={recents}
              onPick={pickFolder}
              onClose={() => setMenuOpen(false)}
            />
          )}
        </div>

        <span className="w-px h-5 mx-1.5 bg-[var(--color-border)]" />

        {/* shells at this location */}
        <div className="flex items-center gap-1 min-w-0 overflow-x-auto">
          {here.map((tab) => {
            const isShown = tab.id === shown?.id;
            return (
              <div
                key={tab.id}
                data-testid={`terminal-tab-${tab.id}`}
                onClick={() => setActiveTab(tab.id)}
                className={`group flex items-center gap-2 h-[26px] pl-2.5 pr-1.5 rounded-md cursor-pointer whitespace-nowrap ${
                  isShown
                    ? 'bg-[var(--color-hover)] text-[var(--color-text)]'
                    : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${tab.status === 'exited' ? 'bg-[var(--color-text-muted)]' : 'bg-green-500'}`}
                />
                <span className="truncate max-w-[120px]">{tab.title}</span>
                <button
                  type="button"
                  data-testid={`terminal-close-${tab.id}`}
                  aria-label={`Close terminal ${tab.title}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    closeHere(tab.id);
                  }}
                  className="flex items-center justify-center w-4 h-4 rounded opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:bg-[var(--color-border)]"
                >
                  <span className="material-symbols-outlined icon-xs">close</span>
                </button>
              </div>
            );
          })}
        </div>
        <button
          type="button"
          data-testid="terminal-new"
          aria-label="New terminal"
          title={`New terminal in ${currentSeg.label}`}
          onClick={() => openTerminal({ cwd: current.cwd })}
          className={ICON_BTN}
        >
          <span className="material-symbols-outlined icon-sm">add</span>
        </button>

        <div className="flex-1" />
        <button
          type="button"
          data-testid="terminal-attach"
          aria-label="Add terminal output to chat"
          title="Add the selection (or the last 200 lines) to the chat input"
          disabled={!shown}
          onClick={sendToChat}
          className={`${ICON_BTN} ${sentFrom && sentFrom === shown?.id ? '!text-green-500' : ''}`}
        >
          <span className="material-symbols-outlined icon-sm">
            {sentFrom && sentFrom === shown?.id ? 'check' : 'add_comment'}
          </span>
        </button>
        <button
          type="button"
          data-testid="terminal-collapse"
          aria-label="Hide terminal panel"
          title="Hide (⌘`)"
          onClick={() => setPanelOpen(false)}
          className={ICON_BTN}
        >
          <span className="material-symbols-outlined icon-sm">keyboard_arrow_down</span>
        </button>
      </div>

      {/* terminal surfaces (all mounted; only the shown one visible) */}
      <div
        className="flex-1 relative overflow-hidden"
        // An unmodified ↵ typed into an exited shell restarts it (xterm's textarea
        // keydown bubbles here); ⌘↵ / a ↵ on the bar's buttons are left alone.
        onKeyDownCapture={(e) => {
          if (shown?.status !== 'exited' || e.key !== 'Enter') return;
          if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
          const target = e.target as HTMLElement | null;
          if (target?.closest?.('button')) return;
          e.preventDefault();
          e.stopPropagation();
          restartTerminal(shown.id);
        }}
      >
        {!shown && (
          <div className="h-full flex flex-col items-center justify-center gap-2 text-[12px] text-[var(--color-text-muted)]">
            <span>No terminal in {currentSeg.label}</span>
            <button
              type="button"
              data-testid="terminal-empty-open"
              onClick={() => openTerminal({ cwd: current.cwd })}
              className="btn-md btn-secondary"
            >
              <span className="material-symbols-outlined icon-sm">add</span>
              Open terminal
            </button>
          </div>
        )}
        {tabs.map((tab) => {
          const isShown = tab.id === shown?.id;
          return (
            // The WRAPPER — not just the inner TerminalTab — must be hidden when not
            // shown. All wrappers are `absolute inset-0` and stack in DOM order; an
            // inactive one left visible is a transparent overlay that swallows
            // every mousedown meant for the shown terminal. display:none removes it
            // from hit-testing; background PTY streaming is unaffected (the read
            // loop lives in the pty service and xterm buffers while hidden).
            <div
              key={tab.id}
              className="absolute inset-0"
              style={{
                display: isShown ? 'block' : 'none',
                // Counter-zoom: cancel the app-wide CSS zoom (useZoom sets `zoom` on
                // <html>) so this subtree's net scale is 1.0 — load-bearing for xterm
                // mouse selection, which maps clientX to a column with an unscaled
                // cell width. `--app-zoom-inv` is the precomputed reciprocal
                // (useZoom.applyZoom). Scoped to the per-tab wrapper so the panel's
                // drag handle stays in the un-counter-zoomed coordinate space;
                // FitAddon re-fits through the host's ResizeObserver.
                zoom: 'var(--app-zoom-inv, 1)',
              }}
            >
              <TerminalTab tab={tab} active={isShown} />
              {isShown && tab.status === 'exited' && (
                <div
                  data-testid={`terminal-exited-${tab.id}`}
                  className="absolute left-0 right-0 bottom-0 flex items-center gap-3 px-3 h-7 text-[11.5px] bg-[var(--color-bg-chrome)] border-t border-[var(--color-border)] text-[var(--color-text-muted)]"
                >
                  <span>Process exited{typeof tab.exitCode === 'number' && tab.exitCode >= 0 ? ` (code ${tab.exitCode})` : ''}</span>
                  <button
                    type="button"
                    data-testid={`terminal-restart-${tab.id}`}
                    onClick={() => restartTerminal(tab.id)}
                    className="btn-sm btn-ghost text-[var(--color-primary)]"
                  >
                    ↵ Restart
                  </button>
                  <button
                    type="button"
                    onClick={() => closeHere(tab.id)}
                    className="btn-sm btn-ghost"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      </div>
    </div>
  );
}
