/**
 * Thread-Scoped Cognitive Context (TSCC) panel component.
 *
 * Renders above the chat input as a collapsible cognitive context panel
 * showing live, thread-specific state via five cognitive modules.
 *
 * Key exports:
 * - ``TSCCPanel``  — Main panel component with CollapsedBar and ExpandedView
 *
 * Sub-components (internal):
 * - ``CollapsedBar``          — Single-line summary with scope, agents, caps, sources, freshness
 * - ``ExpandedView``          — Five cognitive modules in a scrollable container
 * - ``CurrentContextModule``  — Scope label, thread title, mode tag
 * - ``ActiveAgentsModule``    — Agent list and grouped capabilities
 * - ``WhatAIDoingModule``     — 2–4 bullet points of current activity
 * - ``ActiveSourcesModule``   — Source list with origin tags
 * - ``KeySummaryModule``      — 3–5 bullet summary points
 */

import { useState, useEffect } from 'react';
import type { TSCCState, ThreadLifecycleState } from '../../../types';

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
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/** Capability summary for collapsed bar (up to 2 names). */
function capSummary(caps: TSCCState['liveState']['activeCapabilities']): string {
  const all = [...caps.skills, ...caps.mcps, ...caps.tools];
  if (all.length === 0) return '';
  if (all.length <= 2) return all.join(', ');
  return `${all[0]}, ${all[1]} +${all.length - 2}`;
}

// ---------------------------------------------------------------------------
// CollapsedBar
// ---------------------------------------------------------------------------
function CollapsedBar({
  tsccState,
  isPinned,
  onToggleExpand,
  onTogglePin,
}: {
  tsccState: TSCCState;
  isPinned: boolean;
  onToggleExpand: () => void;
  onTogglePin: () => void;
}) {
  const ls = tsccState.liveState;
  const agentCount = ls.activeAgents.length;
  const sourceCount = ls.activeSources.length;
  const caps = capSummary(ls.activeCapabilities);

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
        bg-[var(--color-surface)] border border-[var(--color-border)]
        hover:bg-[var(--color-hover)] transition-colors text-sm select-none"
    >
      <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
        psychology
      </span>
      <span className="text-[var(--color-text)] font-medium truncate">
        {ls.context.scopeLabel}
      </span>
      {agentCount > 0 && (
        <span className="text-[var(--color-text-muted)]">
          {agentCount} agent{agentCount !== 1 ? 's' : ''}
        </span>
      )}

      {caps && (
        <span className="text-[var(--color-text-muted)] truncate max-w-[160px]">
          {caps}
        </span>
      )}
      {sourceCount > 0 && (
        <span className="text-[var(--color-text-muted)]">
          {sourceCount} source{sourceCount !== 1 ? 's' : ''}
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
        <span className="material-symbols-outlined text-base">
          {isPinned ? 'push_pin' : 'push_pin'}
        </span>
      </button>
      <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
        expand_more
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cognitive Modules (ExpandedView sub-components)
// ---------------------------------------------------------------------------

function CurrentContextModule({ tsccState }: { tsccState: TSCCState }) {
  const ctx = tsccState.liveState.context;
  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        Context
      </h4>
      <p className="text-sm text-[var(--color-text)]">{ctx.scopeLabel}</p>
      {ctx.threadTitle && (
        <p className="text-sm text-[var(--color-text-muted)]">{ctx.threadTitle}</p>
      )}
      {ctx.mode && (
        <span className="inline-block mt-1 px-2 py-0.5 text-xs rounded-full bg-primary/10 text-primary">
          {ctx.mode}
        </span>
      )}
    </div>
  );
}

function ActiveAgentsModule({ tsccState }: { tsccState: TSCCState }) {
  const ls = tsccState.liveState;
  const caps = ls.activeCapabilities;
  const hasAgents = ls.activeAgents.length > 0;

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        Active Agents
      </h4>
      {!hasAgents ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          Using core SwarmAgent only
        </p>
      ) : (
        <div className="flex flex-wrap gap-1 mb-1">
          {ls.activeAgents.map((a) => (
            <span key={a} className="px-2 py-0.5 text-xs rounded-full bg-[var(--color-surface-alt)] text-[var(--color-text)]">
              {a}
            </span>
          ))}
        </div>
      )}

      {caps.skills.length > 0 && (
        <div className="text-xs text-[var(--color-text-muted)]">
          Skills: {caps.skills.join(', ')}
        </div>
      )}
      {caps.mcps.length > 0 && (
        <div className="text-xs text-[var(--color-text-muted)]">
          MCPs: {caps.mcps.join(', ')}
        </div>
      )}
      {caps.tools.length > 0 && (
        <div className="text-xs text-[var(--color-text-muted)]">
          Tools: {caps.tools.join(', ')}
        </div>
      )}
    </div>
  );
}

function WhatAIDoingModule({ tsccState }: { tsccState: TSCCState }) {
  const doing = tsccState.liveState.whatAiDoing;
  const isIdle =
    tsccState.lifecycleState === 'idle' ||
    tsccState.lifecycleState === 'paused' ||
    tsccState.lifecycleState === 'new';

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        What AI is Doing
      </h4>
      {doing.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          {isIdle ? 'Waiting for your input' : 'Processing...'}
        </p>
      ) : (
        <ul className="list-disc list-inside text-sm text-[var(--color-text)] space-y-0.5">
          {doing.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActiveSourcesModule({ tsccState }: { tsccState: TSCCState }) {
  const sources = tsccState.liveState.activeSources;

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        Active Sources
      </h4>
      {sources.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          Using conversation context only
        </p>
      ) : (
        <ul className="text-sm space-y-0.5">
          {sources.map((s, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="text-[var(--color-text)]">{s.path}</span>
              <span className="px-1.5 py-0 text-[10px] rounded bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]">
                {s.origin}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function KeySummaryModule({ tsccState }: { tsccState: TSCCState }) {
  const summary = tsccState.liveState.keySummary;

  return (
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
        Key Summary
      </h4>
      {summary.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)] italic">
          No summary yet — ask me to summarize this thread
        </p>
      ) : (
        <ul className="list-disc list-inside text-sm text-[var(--color-text)] space-y-0.5">
          {summary.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
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
}: {
  tsccState: TSCCState;
  isPinned: boolean;
  onToggleExpand: () => void;
  onTogglePin: () => void;
}) {
  // Transient "Resumed" indicator after cancelled→active
  const [showResumed, _setShowResumed] = useState(false);

  useEffect(() => {
    if (tsccState.lifecycleState === 'active') {
      // We can't easily detect the transition from cancelled→active here
      // without tracking previous state, so we rely on the parent to
      // call setAutoExpand which triggers this render.
    }
  }, [tsccState.lifecycleState]);

  return (
    <div
      role="region"
      aria-label="Thread cognitive context"
      aria-expanded={true}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden"
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
          Cognitive Context
        </span>
        <span
          className="ml-1 text-xs text-[var(--color-text-muted)]"
          aria-live="polite"
        >
          {showResumed
            ? 'Resumed · Continuing previous analysis'
            : lifecycleLabel(tsccState.lifecycleState)}
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
            <span className="material-symbols-outlined text-base">push_pin</span>
          </button>
          <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
            expand_less
          </span>
        </div>
      </div>

      {/* Scrollable modules */}
      <div className="px-3 pb-3 space-y-3 max-h-[280px] overflow-y-auto">
        <CurrentContextModule tsccState={tsccState} />
        <hr className="border-[var(--color-border)]" />
        <ActiveAgentsModule tsccState={tsccState} />
        <hr className="border-[var(--color-border)]" />
        <WhatAIDoingModule tsccState={tsccState} />
        <hr className="border-[var(--color-border)]" />
        <ActiveSourcesModule tsccState={tsccState} />
        <hr className="border-[var(--color-border)]" />
        <KeySummaryModule tsccState={tsccState} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------
/** Default TSCC state shown before a session is created (Req 1.2, 9.1). */
const DEFAULT_TSCC_STATE: TSCCState = {
  threadId: '',
  projectId: null,
  scopeType: 'workspace',
  lastUpdatedAt: new Date().toISOString(),
  lifecycleState: 'new',
  liveState: {
    context: {
      scopeLabel: 'Workspace: SwarmWS (General)',
      threadTitle: '',
    },
    activeAgents: [],
    activeCapabilities: { skills: [], mcps: [], tools: [] },
    whatAiDoing: [],
    activeSources: [],
    keySummary: [],
  },
};

export function TSCCPanel({
  threadId: _threadId,
  tsccState,
  isExpanded,
  isPinned,
  onToggleExpand,
  onTogglePin,
}: TSCCPanelProps) {
  // Show default "new" state when no session exists yet (Req 1.2, 9.1)
  const effectiveState = tsccState ?? DEFAULT_TSCC_STATE;

  return (
    <div className="px-4 py-2 flex-shrink-0">
      {isExpanded ? (
        <ExpandedView
          tsccState={effectiveState}
          isPinned={isPinned}
          onToggleExpand={onToggleExpand}
          onTogglePin={onTogglePin}
        />
      ) : (
        <CollapsedBar
          tsccState={effectiveState}
          isPinned={isPinned}
          onToggleExpand={onToggleExpand}
          onTogglePin={onTogglePin}
        />
      )}
    </div>
  );
}
