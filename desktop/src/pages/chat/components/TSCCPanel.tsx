/**
 * Thread-Scoped Cognitive Context (TSCC) panel component.
 *
 * Renders above the chat input as a collapsible panel showing system prompt
 * metadata: loaded context files, token counts, and a "View Full Prompt"
 * action via the ``SystemPromptModule``.
 *
 * Key exports:
 * - ``TSCCPanel``              — Main panel component
 * - ``createDefaultTSCCState`` — Factory for default TSCC state (kept for test compat)
 *
 * Sub-components (internal):
 * - ``CollapsedBar``  — Single-line summary with file count and total tokens
 * - ``ExpandedView``  — SystemPromptModule in a scrollable container
 *
 * Requirements: 6.1, 6.2, 6.3
 */

import { useMemo } from 'react';
import type { TSCCState, ThreadLifecycleState, SystemPromptMetadata } from '../../../types';
import { SystemPromptModule } from './TSCCModules';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
interface TSCCPanelProps {
  threadId: string | null;
  tsccState: TSCCState | null;
  isExpanded: boolean;
  isPinned: boolean;
  onToggleExpand: () => void;
  onTogglePin: () => void;
  sessionId?: string | null;
  promptMetadata?: SystemPromptMetadata | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Human-readable lifecycle label. */
function lifecycleLabel(state: ThreadLifecycleState): string {
  switch (state) {
    case 'new':
      return 'New thread · Ready';
    case 'active':
      return 'Updated just now';
    case 'paused':
      return 'Paused · Waiting for your input';
    case 'failed':
      return 'Something went wrong — see details below';
    case 'cancelled':
      return 'Execution stopped · Partial progress saved';
    case 'idle':
      return 'Idle · Ready for next task';
  }
}

/** Relative freshness string from ISO timestamp. */
function freshness(iso: string): string {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return '';
  const diff = Date.now() - time;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

// ---------------------------------------------------------------------------
// CollapsedBar
// ---------------------------------------------------------------------------
function CollapsedBar({
  tsccState,
  isPinned,
  onToggleExpand,
  onTogglePin,
  promptMetadata,
}: {
  tsccState: TSCCState;
  isPinned: boolean;
  onToggleExpand: () => void;
  onTogglePin: () => void;
  promptMetadata?: SystemPromptMetadata | null;
}) {
  const fileCount = promptMetadata?.files?.length ?? 0;
  const totalTokens = promptMetadata?.totalTokens ?? 0;

  return (
    <div
      role="region"
      aria-label="Thread cognitive context"
      aria-expanded={false}
      tabIndex={0}
      onClick={onToggleExpand}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggleExpand();
        }
      }}
      className="flex items-center gap-3 px-3 py-2 cursor-pointer rounded-lg
        bg-[var(--color-card)] border border-[var(--color-border)]
        hover:bg-[var(--color-hover)] transition-colors text-sm select-none"
    >
      <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
        psychology
      </span>
      <span className="text-[var(--color-text)] font-medium truncate">
        System Prompt
      </span>
      {fileCount > 0 && (
        <span className="text-[var(--color-text-muted)]">
          {fileCount} file{fileCount !== 1 ? 's' : ''}
        </span>
      )}
      {totalTokens > 0 && (
        <span className="text-[var(--color-text-muted)]">
          {totalTokens.toLocaleString()} tok
        </span>
      )}
      <span className="ml-auto text-xs text-[var(--color-text-muted)]">
        {freshness(tsccState.lastUpdatedAt)}
      </span>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onTogglePin();
        }}
        className={`p-1 rounded transition-colors ${
          isPinned
            ? 'text-primary'
            : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
        }`}
        aria-label={isPinned ? 'Unpin panel' : 'Pin panel'}
        aria-pressed={isPinned}
      >
        <span
          className={`material-symbols-outlined text-base ${isPinned ? '' : 'rotate-45'}`}
          style={isPinned ? { fontVariationSettings: "'FILL' 1" } : undefined}
        >
          push_pin
        </span>
      </button>
      <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
        expand_more
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ExpandedView
// ---------------------------------------------------------------------------
function ExpandedView({
  tsccState,
  isPinned,
  onToggleExpand,
  onTogglePin,
  sessionId,
  promptMetadata,
}: {
  tsccState: TSCCState;
  isPinned: boolean;
  onToggleExpand: () => void;
  onTogglePin: () => void;
  sessionId?: string | null;
  promptMetadata?: SystemPromptMetadata | null;
}) {
  return (
    <div
      role="region"
      aria-label="Thread cognitive context"
      aria-expanded={true}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] overflow-hidden"
    >
      {/* Header bar */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-[var(--color-hover)] transition-colors"
        onClick={onToggleExpand}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggleExpand();
          }
        }}
      >
        <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
          psychology
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">
          System Prompt
        </span>
        <span
          className="ml-1 text-xs text-[var(--color-text-muted)]"
          aria-live="polite"
        >
          {lifecycleLabel(tsccState.lifecycleState)}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onTogglePin();
            }}
            className={`p-1 rounded transition-colors ${
              isPinned
                ? 'text-primary'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
            aria-label={isPinned ? 'Unpin panel' : 'Pin panel'}
            aria-pressed={isPinned}
          >
            <span
              className={`material-symbols-outlined text-base ${isPinned ? '' : 'rotate-45'}`}
              style={isPinned ? { fontVariationSettings: "'FILL' 1" } : undefined}
            >
              push_pin
            </span>
          </button>
          <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
            expand_less
          </span>
        </div>
      </div>

      {/* Scrollable content — single SystemPromptModule */}
      <div className="px-3 pb-3 space-y-3 max-h-[280px] overflow-y-auto">
        <SystemPromptModule
          sessionId={sessionId ?? null}
          metadata={promptMetadata ?? null}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------
/** Default scope label shown when no session context exists yet. */
const DEFAULT_SCOPE_LABEL = 'Workspace: SwarmWS (General)';

/**
 * Factory for default TSCC state — generates a fresh ``lastUpdatedAt``
 * timestamp on every call so freshness calculations are accurate.
 * Kept for backward compatibility with existing tests.
 */
export function createDefaultTSCCState(): TSCCState {
  return {
    threadId: '',
    projectId: null,
    scopeType: 'workspace',
    lastUpdatedAt: new Date().toISOString(),
    lifecycleState: 'new',
    liveState: {
      context: {
        scopeLabel: DEFAULT_SCOPE_LABEL,
        threadTitle: '',
      },
      activeAgents: [],
      activeCapabilities: { skills: [], mcps: [], tools: [] },
      whatAiDoing: [],
      activeSources: [],
      keySummary: [],
    },
  };
}

export function TSCCPanel({
  threadId: _threadId,
  tsccState,
  isExpanded,
  isPinned,
  onToggleExpand,
  onTogglePin,
  sessionId,
  promptMetadata,
}: TSCCPanelProps) {
  // Show default "new" state when no session exists yet
  const effectiveState = useMemo(
    () => tsccState ?? createDefaultTSCCState(),
    [tsccState]
  );

  return (
    <div className="px-4 py-2 flex-shrink-0">
      {isExpanded ? (
        <ExpandedView
          tsccState={effectiveState}
          isPinned={isPinned}
          onToggleExpand={onToggleExpand}
          onTogglePin={onTogglePin}
          sessionId={sessionId}
          promptMetadata={promptMetadata}
        />
      ) : (
        <CollapsedBar
          tsccState={effectiveState}
          isPinned={isPinned}
          onToggleExpand={onToggleExpand}
          onTogglePin={onTogglePin}
          promptMetadata={promptMetadata}
        />
      )}
    </div>
  );
}
