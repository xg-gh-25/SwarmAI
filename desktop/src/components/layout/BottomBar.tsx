/**
 * BottomBar -- thin status bar spanning full app width below all 3 columns.
 *
 * Displays:
 * - Left: connection status dot, agent name, workspace name, app version, code intel health
 * - Right: keyboard shortcut hints with badge styling
 *
 * Uses raw useContext (not useHealth) to avoid crashes when HealthProvider
 * is not in the tree (e.g. in isolated component tests).
 */

import { useContext, useState, useEffect, useRef, useCallback, useSyncExternalStore } from 'react';
import { HealthContext } from '../../contexts/HealthContext';
import { useSessionMeta } from '../../contexts/LayoutContext';
import { isDesktop } from '../../services/tauri';
import { getCodeIntelSummary, triggerReindex, type CodeIntelSummary } from '../../services/codeIntel';
import { CodeGraph } from '../code-intel/CodeGraph';
import { terminalStore } from '../../stores/TerminalStore';
import { TERMINAL_TOGGLE_EVENT } from '../../contexts/TerminalContext';

// ── Code Intel Popover ───────────────────────────────────────────────────────

function CodeIntelPopover({ summary, onReindex, isReindexing, onViewGraph }: {
  summary: CodeIntelSummary;
  onReindex: () => void;
  isReindexing: boolean;
  onViewGraph: () => void;
}) {
  const topLang = Object.entries(summary.languages)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([lang, count]) => `${lang} ${summary.symbolCount > 0 ? Math.round(count / summary.symbolCount * 100) : 0}%`)
    .join(', ');

  return (
    <div
      className="absolute bottom-full left-0 mb-1 w-[320px] bg-[var(--color-bg-elevated,var(--color-bg-chrome))] border border-[var(--color-border)] rounded-md shadow-lg p-3 text-[11px] text-[var(--color-text)] z-50"
      data-testid="code-intel-popover"
    >
      <div className="font-medium text-[12px] mb-2">Code Intelligence — SwarmAI</div>
      <table className="w-full">
        <tbody className="[&_td]:py-0.5">
          <tr><td className="text-[var(--color-text-muted)]">Symbols</td><td className="text-right font-mono">{summary.symbolCount.toLocaleString()}</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Edges</td><td className="text-right font-mono">{summary.edgeCount.toLocaleString()}</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Files</td><td className="text-right font-mono">{summary.fileCount.toLocaleString()}</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Entry Points</td><td className="text-right font-mono">{summary.entryPoints.toLocaleString()}</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Unused Exports</td><td className="text-right font-mono">{summary.unusedExportsCount.toLocaleString()} ({summary.unusedExportsPct}%)</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Languages</td><td className="text-right">{topLang}</td></tr>
          <tr><td className="text-[var(--color-text-muted)]">Last Indexed</td><td className="text-right">{summary.lastIndexedAt ? _formatAge(summary.lastIndexedAt) : '—'}</td></tr>
        </tbody>
      </table>
      <div className="mt-2.5 flex gap-1.5">
        <button
          onClick={onViewGraph}
          className="flex-1 text-center py-1 px-2 rounded border border-indigo-500/50 hover:bg-indigo-500/20 text-[10px] text-indigo-300"
        >
          View Graph
        </button>
        <button
          onClick={onReindex}
          disabled={isReindexing}
          className="flex-1 text-center py-1 px-2 rounded border border-[var(--color-border)] hover:bg-[var(--color-hover)] disabled:opacity-50 disabled:cursor-not-allowed text-[10px]"
        >
          {isReindexing ? 'Indexing...' : 'Re-index'}
        </button>
      </div>
    </div>
  );
}

function _formatAge(isoStr: string): string {
  try {
    // F5: Ensure timezone-naive strings are treated as UTC (backend stores UTC)
    const normalized = isoStr.includes('+') || isoStr.includes('Z') ? isoStr : isoStr + 'Z';
    const dt = new Date(normalized);
    if (isNaN(dt.getTime())) return '—';
    const diffMs = Date.now() - dt.getTime();
    const days = Math.floor(diffMs / 86400000);
    if (days === 0) return 'today';
    if (days === 1) return '1d ago';
    return `${days}d ago`;
  } catch {
    return '—';
  }
}

// ── Main Component ──────────────────────────────────────────────────────────

export function BottomBar() {
  // Safe: useContext returns undefined when provider is missing (no throw)
  const healthCtx = useContext(HealthContext);
  const healthStatus = healthCtx?.health?.status;
  const isConnected = healthStatus === 'connected';
  // 'degraded' (run_13094a88): the daemon is ALIVE, just briefly stalled — it must
  // NOT render as "Offline" (that IS the false-offline bug this state prevents).
  const isDegraded = healthStatus === 'degraded';
  const { activeSessionMeta } = useSessionMeta();
  const agentName = activeSessionMeta?.agentName || 'Swarm';

  const [appVersion, setAppVersion] = useState('');
  useEffect(() => {
    if (import.meta.env.DEV) {
      setAppVersion('dev');
    } else if (isDesktop()) {
      import('@tauri-apps/api/app').then(m => m.getVersion()).then(setAppVersion).catch(() => {});
    } else {
      fetch('/health', { signal: AbortSignal.timeout(2000) })
        .then(r => r.json())
        .then(d => setAppVersion(d.version || ''))
        .catch(() => {});
    }
  }, []);

  // Integrated-terminal count — subscribe directly to the module-level store
  // (no provider dependence; BottomBar is a sibling of TerminalProvider).
  const terminalCount = useSyncExternalStore(
    (cb) => terminalStore.subscribe(cb),
    () => terminalStore.count(),
  );
  const toggleTerminal = useCallback(() => {
    window.dispatchEvent(new CustomEvent(TERMINAL_TOGGLE_EVENT));
  }, []);

  // Code Intel state
  const [codeIntel, setCodeIntel] = useState<CodeIntelSummary | null>(null);
  const [showPopover, setShowPopover] = useState(false);
  const [showGraph, setShowGraph] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Fetch code intel summary on mount + every 60s
  useEffect(() => {
    let cancelled = false;
    const fetchSummary = () => {
      getCodeIntelSummary('SwarmAI')
        .then(data => { if (!cancelled) setCodeIntel(data); })
        .catch(() => { if (!cancelled) setCodeIntel(null); });
    };
    fetchSummary();
    const interval = setInterval(fetchSummary, 60_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // Listen for 'swarm:show-code-graph' event from LeftSidebar nav icon
  useEffect(() => {
    const handler = () => setShowGraph(true);
    window.addEventListener('swarm:show-code-graph', handler);
    return () => window.removeEventListener('swarm:show-code-graph', handler);
  }, []);

  // Close graph on ESC key (stopPropagation prevents conflicts with other ESC handlers)
  useEffect(() => {
    if (!showGraph) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        e.preventDefault();
        setShowGraph(false);
      }
    };
    // Use capture phase to fire BEFORE other ESC handlers
    document.addEventListener('keydown', handler, true);
    return () => document.removeEventListener('keydown', handler, true);
  }, [showGraph]);

  // Close popover on outside click
  useEffect(() => {
    if (!showPopover) return;
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setShowPopover(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showPopover]);

  const handleReindex = useCallback(() => {
    setIsReindexing(true);
    triggerReindex('SwarmAI')
      .then(() => {
        // Refresh summary after a delay to let background task progress
        setTimeout(() => {
          getCodeIntelSummary('SwarmAI')
            .then(data => { setCodeIntel(data); setIsReindexing(false); })
            .catch(() => setIsReindexing(false));
        }, 5000);
      })
      .catch(() => setIsReindexing(false));
  }, []);

  // Freshness color
  const freshnessColor = codeIntel?.freshnessStatus === 'stale'
    ? 'text-amber-500'
    : 'text-[var(--color-text-dim,var(--color-text-muted))]';

  return (
    <div
      className="h-[26px] bg-[var(--color-bg-chrome)] border-t border-[var(--color-border)] flex items-center px-3.5 text-[10px] text-[var(--color-text-dim,var(--color-text-muted))] select-none flex-shrink-0"
      data-testid="bottom-bar"
    >
      {/* Left: status */}
      <div className="flex items-center gap-2.5">
        <span className="flex items-center gap-1.5">
          <span
            className={`w-[5px] h-[5px] rounded-full flex-shrink-0 ${
              isConnected ? 'bg-green-500' : isDegraded ? 'bg-amber-500' : 'bg-gray-500'
            }`}
            aria-hidden="true"
          />
          <span>{isConnected ? 'Connected' : isDegraded ? 'Reconnecting' : 'Offline'}</span>
        </span>
        <span className="flex items-center gap-1">
          <span className="material-symbols-outlined text-[12px] leading-none">smart_toy</span>
          <span>{agentName}</span>
        </span>
        <span className="flex items-center gap-1">
          <span className="material-symbols-outlined text-[12px] leading-none">folder</span>
          <span>SwarmWS</span>
        </span>
        {appVersion && (
          <span className="opacity-60">v{appVersion}</span>
        )}
        {/* Code Intelligence indicator */}
        {codeIntel && (
          <div className="relative" ref={popoverRef}>
            <button
              onClick={() => setShowPopover(prev => !prev)}
              className={`flex items-center gap-1 hover:opacity-100 opacity-70 transition-opacity cursor-pointer ${freshnessColor}`}
              title="Code Intelligence"
              data-testid="code-intel-indicator"
            >
              <span className="text-[11px]">🧠</span>
              <span className="font-mono">{codeIntel.symbolCount.toLocaleString()}</span>
              <span className="opacity-60">|</span>
              <span>{codeIntel.lastIndexedAt ? _formatAge(codeIntel.lastIndexedAt) : '—'}</span>
            </button>
            {showPopover && (
              <CodeIntelPopover
                summary={codeIntel}
                onReindex={handleReindex}
                isReindexing={isReindexing}
                onViewGraph={() => { setShowGraph(true); setShowPopover(false); }}
              />
            )}
          </div>
        )}
        {/* Integrated terminal toggle + open-count badge (⌘` also toggles). */}
        <button
          onClick={toggleTerminal}
          className="flex items-center gap-1 hover:opacity-100 opacity-70 transition-opacity cursor-pointer text-[var(--color-text-muted)]"
          title="Toggle terminal (⌘`)"
          data-testid="terminal-toggle"
        >
          <span className="text-[11px]">⌘</span>
          <span>Terminal</span>
          {terminalCount > 0 && (
            <span
              className="ml-0.5 px-1 rounded-[7px] bg-[var(--color-accent,#2f81f7)] text-white text-[8px] font-semibold leading-[1.4]"
              data-testid="terminal-badge"
            >
              {terminalCount}
            </span>
          )}
        </button>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Code Intelligence Graph (full-screen overlay) */}
      {showGraph && (
        <CodeGraph project="SwarmAI" onClose={() => setShowGraph(false)} />
      )}

      {/* Right: keyboard hints with badge-style kbd — overflow-hidden allows graceful
          clipping at extreme narrow widths instead of rigid flex-shrink-0 which would
          push the left status section off-screen. pr-1.5 avoids window edge clip. */}
      <div className="flex items-center gap-3 pr-1.5 font-mono text-[9px] overflow-x-hidden">
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1 py-px rounded-[3px]">Enter</kbd>
          <span>send</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1 py-px rounded-[3px]">Shift+Enter</kbd>
          <span>newline</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1 py-px rounded-[3px]">&#8984;N</kbd>
          <span>new</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1 py-px rounded-[3px]">&#8984;1-9</kbd>
          <span>tab</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1 py-px rounded-[3px]">&#8984;`</kbd>
          <span>terminal</span>
        </span>
      </div>
    </div>
  );
}

export default BottomBar;
