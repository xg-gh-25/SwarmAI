/**
 * BottomBar -- thin status bar spanning full app width below all 3 columns.
 *
 * Displays:
 * - Left: connection status dot, agent name, workspace name
 * - Right: keyboard shortcut hints
 *
 * Uses raw useContext (not useHealth) to avoid crashes when HealthProvider
 * is not in the tree (e.g. in isolated component tests).
 */

import { useContext } from 'react';
import { HealthContext } from '../../contexts/HealthContext';
import { useLayout } from '../../contexts/LayoutContext';

export function BottomBar() {
  // Safe: useContext returns undefined when provider is missing (no throw)
  const healthCtx = useContext(HealthContext);
  const isConnected = healthCtx?.health?.status === 'connected';
  const { activeSessionMeta } = useLayout();
  const agentName = activeSessionMeta?.agentName || 'SwarmAI';

  return (
    <div
      className="h-[26px] bg-[var(--color-bg)] border-t border-[var(--color-border)] flex items-center px-3 text-[10px] text-[var(--color-text-muted)] select-none flex-shrink-0"
      data-testid="bottom-bar"
    >
      {/* Left: status */}
      <div className="flex items-center gap-2">
        <span
          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isConnected ? 'bg-green-500' : 'bg-gray-500'}`}
          aria-hidden="true"
        />
        <span>{isConnected ? 'Connected' : 'Offline'}</span>
        <span className="text-[var(--color-border)]" aria-hidden="true">|</span>
        <span className="material-symbols-outlined text-[11px] leading-none">smart_toy</span>
        <span>{agentName}</span>
        <span className="text-[var(--color-border)]" aria-hidden="true">|</span>
        <span className="material-symbols-outlined text-[11px] leading-none">folder</span>
        {/* TODO: read workspace name from config if it becomes user-configurable */}
        <span>SwarmWS</span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right: keyboard hints */}
      <div className="flex items-center gap-3 font-mono text-[var(--color-text-dim)]">
        <span><kbd className="text-[var(--color-text-muted)]">Enter</kbd> send</span>
        <span><kbd className="text-[var(--color-text-muted)]">Shift+Enter</kbd> newline</span>
        <span><kbd className="text-[var(--color-text-muted)]">Cmd+1-9</kbd> tab</span>
      </div>
    </div>
  );
}

export default BottomBar;
