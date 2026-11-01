/**
 * BottomBar -- thin status bar spanning full app width below all 3 columns.
 *
 * Displays:
 * - Left: connection status dot, agent name, workspace name, app version, terminal toggle
 * - Right: keyboard shortcut hints with badge styling
 *
 * Uses raw useContext (not useHealth) to avoid crashes when HealthProvider
 * is not in the tree (e.g. in isolated component tests).
 */

import { useContext, useState, useEffect, useCallback, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { modKeyLabel } from '../../utils/platform';
import { HealthContext } from '../../contexts/HealthContext';
import { useSessionMeta } from '../../contexts/LayoutContext';
import { isDesktop } from '../../services/tauri';
import { terminalStore } from '../../stores/TerminalStore';
import { TERMINAL_TOGGLE_EVENT, TERMINAL_GOTO_EVENT } from '../../contexts/TerminalContext';
import { terminalLocation } from '../../stores/terminalLocation';

// ── Main Component ──────────────────────────────────────────────────────────

export function BottomBar() {
  const { t } = useTranslation();
  const mod = modKeyLabel();
  // Safe: useContext returns undefined when provider is missing (no throw)
  const healthCtx = useContext(HealthContext);
  const healthStatus = healthCtx?.health?.status;
  const isConnected = healthStatus === 'connected';
  // 'degraded' (run_13094a88): the daemon is ALIVE, just briefly stalled — it must
  // NOT render as "Offline" (that IS the false-offline bug this state prevents).
  const isDegraded = healthStatus === 'degraded';
  // 'initializing' = app boot / backend still starting (e.g. right after a
  // deploy restart). It is not a failure, so it must not read as "Offline".
  const isStarting = healthStatus === 'initializing';
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
  // Where a terminal opens for the chat in front (terminalLocation); clicking the
  // chip reveals the panel there.
  const termLoc = useSyncExternalStore(
    (cb) => terminalLocation.subscribe(cb),
    () => terminalLocation.snapshot(),
  ).current;
  const gotoTerminal = useCallback(() => {
    window.dispatchEvent(new CustomEvent(TERMINAL_GOTO_EVENT));
  }, []);

  return (
    <div
      className="h-[30px] bg-[var(--color-bg-chrome)] border-t border-[var(--color-border)] flex items-center px-4 text-[11.5px] text-[var(--color-text-dim,var(--color-text-muted))] select-none flex-shrink-0"
      data-testid="bottom-bar"
    >
      {/* Left: status */}
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5">
          <span
            className={`w-[5px] h-[5px] rounded-full flex-shrink-0 ${
              isConnected
                ? 'bg-green-500'
                : isDegraded
                  ? 'bg-amber-500'
                  : isStarting
                    ? 'bg-amber-500 animate-pulse'
                    : 'bg-gray-500'
            }`}
            aria-hidden="true"
          />
          <span>
            {isConnected ? t('ui.BottomBar.connected') : isDegraded ? t('ui.BottomBar.reconnecting') : isStarting ? t('ui.BottomBar.starting') : t('ui.BottomBar.offline')}
          </span>
        </span>
        <span className="flex items-center gap-1">
          <span className="material-symbols-outlined icon-xs leading-none">smart_toy</span>
          <span>{agentName}</span>
        </span>
        <button
          type="button"
          data-testid="bottom-bar-terminal-location"
          onClick={gotoTerminal}
          title={termLoc.cwd
            ? t('ui.BottomBar.openTerminalInCwd', { label: termLoc.label, cwd: termLoc.cwd })
            : t('ui.BottomBar.openTerminalIn', { label: termLoc.label })}
          className="flex items-center gap-1 h-6 px-2 -mx-1 rounded-md hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] cursor-pointer"
        >
          <span className="material-symbols-outlined icon-xs leading-none">
            {termLoc.kind === 'repo' ? 'code' : termLoc.kind === 'custom' ? 'folder' : 'hive'}
          </span>
          <span className="truncate max-w-[160px]">{termLoc.label}</span>
        </button>
        {appVersion && (
          <span className="opacity-60">v{appVersion}</span>
        )}
        {/* Integrated terminal toggle + open-count badge (⌘`/Ctrl-` also toggles). */}
        <button
          onClick={toggleTerminal}
          className="flex items-center gap-1 h-6 px-2 -mx-1 rounded-md hover:bg-[var(--color-hover)] hover:opacity-100 opacity-80 transition-opacity cursor-pointer text-[var(--color-text-muted)]"
          title={t('nav.toggleTerminal')}
          data-testid="terminal-toggle"
        >
          <span className="text-[11px]">{mod}</span>
          <span>{t('nav.terminal')}</span>
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

      {/* Right: keyboard hints with badge-style kbd — overflow-hidden allows graceful
          clipping at extreme narrow widths instead of rigid flex-shrink-0 which would
          push the left status section off-screen. pr-1.5 avoids window edge clip. */}
      <div className="flex items-center gap-3.5 pr-1.5 tabular-nums text-[11px] overflow-x-hidden">
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5 rounded-[4px]">Enter</kbd>
          <span>{t('nav.hint.send', 'send')}</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5 rounded-[4px]">Shift+Enter</kbd>
          <span>{t('nav.hint.newline', 'newline')}</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5 rounded-[4px]">{mod}1-9</kbd>
          <span>{t('nav.hint.tab', 'tab')}</span>
        </span>
        <span className="flex items-center gap-1">
          <kbd className="bg-[var(--color-hover)] text-[var(--color-text-muted)] border border-[var(--color-border)] px-1.5 py-0.5 rounded-[4px]">{mod}`</kbd>
          <span>{t('nav.hint.terminal', 'terminal')}</span>
        </span>
      </div>
    </div>
  );
}

export default BottomBar;
