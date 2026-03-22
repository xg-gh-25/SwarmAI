/**
 * Sessions section for the Radar sidebar.
 *
 * Displays currently open chat tabs derived from ``openTabs`` and
 * ``tabStatuses`` props (reactive via React re-render, no polling).
 * Each session row shows a title, optional agent name, and a colored
 * status indicator dot.  Sessions with pending questions or permission
 * requests display a small visual badge.
 *
 * Clicking a session row calls ``onTabSelect(tabId)`` to switch the
 * active tab.  A "Chat History" link at the bottom invokes
 * ``onSwitchToHistory()`` to flip the sidebar into History mode.
 *
 * Key exports:
 * - ``SessionsSection``       — The section component
 * - ``STATUS_INDICATOR``      — Status-to-visual-config mapping
 * - ``SessionsSectionProps``  — Props interface
 */

import type { OpenTab } from '../../types';
import type { TabStatus } from '../../../../hooks/useUnifiedTabState';

// ---------------------------------------------------------------------------
// Status indicator configuration
// ---------------------------------------------------------------------------

/**
 * Maps each ``TabStatus`` value to a display label, dot color, and
 * optional CSS animation class for the status indicator.
 */
export const STATUS_INDICATOR: Record<
  TabStatus,
  { label: string; color: string; pulse?: boolean }
> = {
  idle: {
    label: 'Idle',
    color: 'var(--color-text-muted, #9ca3af)',
  },
  streaming: {
    label: 'Streaming',
    color: 'var(--color-success, #22c55e)',
    pulse: true,
  },
  waiting_input: {
    label: 'Waiting for input',
    color: 'var(--color-warning, #f59e0b)',
  },
  permission_needed: {
    label: 'Permission needed',
    color: 'var(--color-warning-alt, #eab308)',
  },
  error: {
    label: 'Error',
    color: 'var(--color-error, #ef4444)',
  },
  complete_unread: {
    label: 'Complete (unread)',
    color: 'var(--color-info, #3b82f6)',
  },
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SessionsSectionProps {
  /** Currently open tabs (derived view from useUnifiedTabState). */
  openTabs: OpenTab[];
  /** Per-tab status map (derived view from useUnifiedTabState). */
  tabStatuses: Record<string, TabStatus>;
  /** Callback to switch the active tab. */
  onTabSelect: (tabId: string) => void;
  /** Callback to switch the sidebar into History mode. */
  onSwitchToHistory: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SessionsSection({
  openTabs,
  tabStatuses,
  onTabSelect,
  onSwitchToHistory,
}: SessionsSectionProps) {
  // --- Empty state ---
  if (openTabs.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">
        No open sessions
      </p>
    );
  }

  // --- Session list ---
  return (
    <div>
      <ul className="space-y-1">
        {[...openTabs].reverse().map((tab) => {
          const status: TabStatus = tabStatuses[tab.id] ?? 'idle';
          const indicator = STATUS_INDICATOR[status];
          const needsAttention =
            status === 'waiting_input' ||
            status === 'permission_needed';

          return (
            <li
              key={tab.id}
              className="flex items-center gap-2 px-1 py-1 rounded
                hover:bg-[var(--color-hover)] transition-colors cursor-pointer"
              onClick={() => onTabSelect(tab.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onTabSelect(tab.id);
                }
              }}
            >
              {/* Status dot */}
              <span
                className={`shrink-0 w-2 h-2 rounded-full${
                  indicator.pulse ? ' animate-pulse' : ''
                }`}
                style={{ backgroundColor: indicator.color }}
                title={indicator.label}
              />

              {/* Title + agent name */}
              <span className="flex flex-col min-w-0 flex-1">
                <span className="text-[13px] leading-5 text-[var(--color-text)] truncate">
                  {tab.title || 'New Chat'}
                </span>
                {tab.agentId && (
                  <span className="text-[10px] text-[var(--color-text-muted)] truncate">
                    {tab.agentId}
                  </span>
                )}
              </span>

              {/* Attention badge for pending question / permission */}
              {needsAttention && (
                <span
                  className="shrink-0 w-4 h-4 flex items-center justify-center
                    rounded-full bg-[var(--color-warning,#f59e0b)] text-white
                    text-[8px] font-bold"
                  title={indicator.label}
                  aria-label={indicator.label}
                >
                  !
                </span>
              )}
            </li>
          );
        })}
      </ul>

      {/* Chat History link */}
      <button
        className="text-xs text-[var(--color-link)] hover:underline mt-2 px-1
          flex items-center gap-1"
        onClick={onSwitchToHistory}
      >
        <span
          className="material-symbols-outlined text-sm"
          aria-hidden="true"
        >
          history
        </span>
        Chat History
      </button>
    </div>
  );
}
