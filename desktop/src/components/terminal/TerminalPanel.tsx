/**
 * TerminalPanel — the VSCode-style bottom terminal panel.
 *
 * Renders a tab bar (one chip per open terminal + a "+" to open a new one +
 * per-tab close) above a stack of TerminalTab surfaces (only the active one is
 * visible; the rest stay mounted so background terminals keep streaming).
 *
 * Layout (Gate-1 C3): this panel is a fixed-height flex sibling that the parent
 * (ThreeColumnLayout) places BELOW the chat row and ABOVE the BottomBar. It has
 * a top drag handle to resize its height. It does NOT overlay chat — chat
 * shrinks by exactly the panel height, so both coexist (AC5). When
 * `panelOpen` is false the parent renders nothing (height 0).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTerminal } from '../../contexts/TerminalContext';
import { terminalStore } from '../../stores/TerminalStore';
import TerminalTab from './TerminalTab';

const MIN_HEIGHT = 120;
const MAX_HEIGHT = 640;
const DEFAULT_HEIGHT = 280;

export default function TerminalPanel() {
  const {
    tabs,
    activeTabId,
    openTerminal,
    closeTerminal,
    setActiveTab,
    setPanelOpen,
  } = useTerminal();

  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [attached, setAttached] = useState<string | null>(null);
  const dragState = useRef<{ startY: number; startH: number } | null>(null);
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null;

  // Auto-open ONE terminal when the panel first opens with no terminals, so the
  // user sees a ready shell (VSCode/Kiro behavior) instead of an empty "click ＋"
  // placeholder. Guarded on terminalStore.count() — the LIVE registry size,
  // which reflects openTerminal's synchronous Map write immediately — NOT the
  // `tabs` snapshot from useSyncExternalStore, whose value is stale within a
  // React commit. Under StrictMode the mount effect runs twice against the same
  // commit; a `tabs.length===0` guard would read 0 both times and spawn TWO
  // shells, but count() reads 1 on the second invocation → exactly one opens.
  useEffect(() => {
    if (terminalStore.count() === 0) {
      openTerminal({});
    }
    // Mount-only: opening a terminal is a one-time "reveal" action, not a
    // reaction to tab changes. openTerminal is stable (useCallback).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      dragState.current = { startY: e.clientY, startH: height };
      const onMove = (ev: MouseEvent) => {
        if (!dragState.current) return;
        // Drag up = taller (panel grows upward from the bottom).
        const delta = dragState.current.startY - ev.clientY;
        const next = Math.min(
          MAX_HEIGHT,
          Math.max(MIN_HEIGHT, dragState.current.startH + delta),
        );
        setHeight(next);
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
      className="flex flex-col flex-shrink-0 border-t-2 border-[var(--color-accent,#2f81f7)] bg-[#0a0d12]"
      style={{ height }}
    >
      {/* drag-to-resize handle */}
      <div
        data-testid="terminal-resize-handle"
        onMouseDown={onDragStart}
        className="h-1 w-full cursor-ns-resize hover:bg-[var(--color-accent,#2f81f7)]"
      />

      {/* tab bar */}
      <div className="flex items-center h-8 bg-[var(--color-bg-chrome,#161b22)] border-b border-[var(--color-border,#30363d)] px-1 gap-0.5">
        {tabs.map((tab) => {
          const isActive = tab.id === activeTabId;
          return (
            <div
              key={tab.id}
              data-testid={`terminal-tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`group flex items-center gap-1.5 px-2.5 py-1 rounded cursor-pointer text-[12px] ${
                isActive
                  ? 'bg-[var(--color-hover,#21262d)] text-[var(--color-text,#e6edf3)]'
                  : 'text-[var(--color-text-muted,#7d8590)] hover:text-[var(--color-text,#e6edf3)]'
              }`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  tab.status === 'exited'
                    ? 'bg-[var(--color-text-muted,#7d8590)]'
                    : 'bg-[var(--color-green,#3fb950)]'
                }`}
              />
              <span className="truncate max-w-[140px]">{tab.title}</span>
              <button
                data-testid={`terminal-close-${tab.id}`}
                aria-label={`Close terminal ${tab.title}`}
                onClick={(e) => {
                  e.stopPropagation();
                  closeTerminal(tab.id);
                }}
                className="opacity-0 group-hover:opacity-70 hover:opacity-100 text-[14px] leading-none"
              >
                ×
              </button>
            </div>
          );
        })}
        <button
          data-testid="terminal-new"
          aria-label="New terminal"
          onClick={() => openTerminal({})}
          className="ml-1 px-2 py-1 rounded text-[16px] leading-none text-[var(--color-text-muted,#7d8590)] hover:bg-[var(--color-hover,#21262d)] hover:text-[var(--color-text,#e6edf3)]"
        >
          ＋
        </button>
        <div className="flex-1" />
        {/* P2: attach the active terminal's output to the chat (one-shot). */}
        <button
          data-testid="terminal-attach"
          aria-label="Attach terminal output to chat"
          title="Attach this terminal's output to your next chat message"
          disabled={!activeTab}
          onClick={() => {
            if (!activeTab?.getBuffer) return;
            const bufferTail = activeTab.getBuffer();
            if (!bufferTail) return;
            window.dispatchEvent(
              new CustomEvent('swarm:attach-terminal', {
                detail: { bufferTail, cwd: activeTab.cwd ?? '' },
              }),
            );
            setAttached(activeTab.id);
            window.setTimeout(() => setAttached((cur) => (cur === activeTab.id ? null : cur)), 2000);
          }}
          className={`px-2 py-1 rounded text-[11px] ${
            attached === activeTabId && activeTabId
              ? 'text-[var(--color-green,#3fb950)]'
              : 'text-[var(--color-text-muted,#7d8590)] hover:bg-[var(--color-hover,#21262d)] hover:text-[var(--color-text,#e6edf3)] disabled:opacity-40'
          }`}
        >
          {attached === activeTabId && activeTabId ? '✓ attached' : '⇪ attach to chat'}
        </button>
        <button
          data-testid="terminal-collapse"
          aria-label="Hide terminal panel"
          onClick={() => setPanelOpen(false)}
          className="px-2 py-1 rounded text-[13px] text-[var(--color-text-muted,#7d8590)] hover:bg-[var(--color-hover,#21262d)] hover:text-[var(--color-text,#e6edf3)]"
        >
          ▾
        </button>
      </div>

      {/* terminal surfaces (all mounted; only active visible) */}
      <div className="flex-1 relative overflow-hidden">
        {tabs.length === 0 ? (
          <div className="h-full flex items-center justify-center text-[12px] text-[var(--color-text-muted,#7d8590)]">
            No terminals — click ＋ to open one
          </div>
        ) : (
          tabs.map((tab) => (
            <div key={tab.id} className="absolute inset-0">
              <TerminalTab tab={tab} active={tab.id === activeTabId} />
            </div>
          ))
        )}
      </div>
    </div>
  );
}
