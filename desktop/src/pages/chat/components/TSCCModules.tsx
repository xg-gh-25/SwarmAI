/**
 * Extracted TSCC cognitive module components.
 *
 * These five modules were extracted from ``TSCCPanel.tsx`` so they can be
 * reused by the new ``TSCCPopoverButton`` without importing the full panel.
 *
 * Key exports:
 * - ``CurrentContextModule``  — Scope label, thread title, mode tag
 * - ``ActiveAgentsModule``    — Agent list and grouped capabilities
 * - ``WhatAIDoingModule``     — 2–4 bullet points of current activity
 * - ``ActiveSourcesModule``   — Source list with origin tags
 * - ``KeySummaryModule``      — 3–5 bullet summary points
 *
 * Shared helpers:
 * - ``lifecycleLabel``  — Human-readable lifecycle label
 * - ``freshness``       — Relative freshness string from ISO timestamp
 * - ``capSummary``      — Capability summary (up to 2 names)
 */

import type { TSCCState, ThreadLifecycleState } from '../../../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Human-readable lifecycle label. */
export function lifecycleLabel(state: ThreadLifecycleState): string {
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
export function freshness(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/** Capability summary for collapsed bar (up to 2 names). */
export function capSummary(caps: TSCCState['liveState']['activeCapabilities']): string {
  const all = [...caps.skills, ...caps.mcps, ...caps.tools];
  if (all.length === 0) return '';
  if (all.length <= 2) return all.join(', ');
  return `${all[0]}, ${all[1]} +${all.length - 2}`;
}

// ---------------------------------------------------------------------------
// Cognitive Modules
// ---------------------------------------------------------------------------

export function CurrentContextModule({ tsccState }: { tsccState: TSCCState }) {
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

export function ActiveAgentsModule({ tsccState }: { tsccState: TSCCState }) {
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
            <span key={a} className="px-2 py-0.5 text-xs rounded-full bg-[var(--color-hover)] text-[var(--color-text)]">
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

export function WhatAIDoingModule({ tsccState }: { tsccState: TSCCState }) {
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

export function ActiveSourcesModule({ tsccState }: { tsccState: TSCCState }) {
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
              <span className="px-1.5 py-0 text-[10px] rounded bg-[var(--color-hover)] text-[var(--color-text-muted)]">
                {s.origin}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function KeySummaryModule({ tsccState }: { tsccState: TSCCState }) {
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
