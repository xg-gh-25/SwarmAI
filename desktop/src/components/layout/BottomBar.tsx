/**
 * BottomBar -- thin status bar spanning full app width below all 3 columns.
 *
 * Displays:
 * - Left: connection status dot, agent name, workspace name
 * - Right: keyboard shortcut hints with badge styling
 *
 * Uses raw useContext (not useHealth) to avoid crashes when HealthProvider
 * is not in the tree (e.g. in isolated component tests).
 */

import { useContext } from 'react';
import { HealthContext } from '../../contexts/HealthContext';
import { useSessionMeta } from '../../contexts/LayoutContext';

export function BottomBar() {
  // Safe: useContext returns undefined when provider is missing (no throw)
  const healthCtx = useContext(HealthContext);
  const isConnected = healthCtx?.health?.status === 'connected';
  const { activeSessionMeta } = useSessionMeta();
  const agentName = activeSessionMeta?.agentName || 'Swarm';

  return (
    <div
      className="h-[26px] bg-[var(--color-bg-chrome)] border-t border-[var(--color-border)] flex items-center px-3.5 text-[10px] text-[var(--color-text-dim,var(--color-text-muted))] select-none flex-shrink-0"
      data-testid="bottom-bar"
    >
      {/* Left: status */}
      <div className="flex items-center gap-2.5">
        <span className="flex items-center gap-1.5">
          <span
            className={`w-[5px] h-[5px] rounded-full flex-shrink-0 ${isConnected ? 'bg-green-500' : 'bg-gray-500'}`}
            aria-hidden="true"
          />
          <span>{isConnected ? 'Connected' : 'Offline'}</span>
        </span>
        <span className="flex items-center gap-1">
          <span className="material-symbols-outlined text-[12px] leading-none">smart_toy</span>
          <span>{agentName}</span>
        </span>
        <span className="flex items-center gap-1">
          <span className="material-symbols-outlined text-[12px] leading-none">folder</span>
          {/* SwarmWS is the canonical agent workspace name (single-workspace app) */}
          <span>SwarmWS</span>
        </span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Right: keyboard hints with badge-style kbd */}
      <div className="flex items-center gap-3 font-mono text-[9px]">
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
      </div>
    </div>
  );
}

export default BottomBar;
