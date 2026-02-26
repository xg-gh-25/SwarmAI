/**
 * TSCC Snapshot Card component for inline thread history display.
 *
 * Renders a collapsible card showing a point-in-time capture of TSCC state.
 * Collapsed by default: shows timestamp and trigger reason.
 * Expanded: shows agents, capabilities (grouped), sources with origin tags,
 * activity description, and key summary.
 *
 * Key exports:
 * - ``TSCCSnapshotCard`` — Main snapshot card component
 */

import { useState } from 'react';
import type { TSCCSnapshot } from '../../../types';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
interface TSCCSnapshotCardProps {
  snapshot: TSCCSnapshot;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format ISO timestamp to a readable date/time string. */
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------
export function TSCCSnapshotCard({ snapshot }: TSCCSnapshotCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const caps = snapshot.activeCapabilities;

  return (
    <div
      className="mx-4 my-2 rounded-lg border border-[var(--color-border)]
        bg-[var(--color-surface)] text-sm"
      role="article"
      aria-label={`Snapshot: ${snapshot.reason}`}
    >
      {/* Collapsed header — always visible */}
      <button
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left
          hover:bg-[var(--color-hover)] transition-colors rounded-lg"
        aria-expanded={isExpanded}
      >
        <span className="material-symbols-outlined text-base text-[var(--color-text-muted)]">
          camera
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          {formatTimestamp(snapshot.timestamp)}
        </span>
        <span className="text-[var(--color-text)] truncate">
          {snapshot.reason}
        </span>
        <span className="ml-auto material-symbols-outlined text-base text-[var(--color-text-muted)]">
          {isExpanded ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {/* Expanded details */}
      {isExpanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-[var(--color-border)]">
          {/* Agents */}
          {snapshot.activeAgents.length > 0 && (
            <div className="pt-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
                Agents
              </h4>
              <div className="flex flex-wrap gap-1">
                {snapshot.activeAgents.map((a) => (
                  <span
                    key={a}
                    className="px-2 py-0.5 text-xs rounded-full
                      bg-[var(--color-surface-alt)] text-[var(--color-text)]"
                  >
                    {a}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Capabilities (grouped) */}
          {(caps.skills.length > 0 ||
            caps.mcps.length > 0 ||
            caps.tools.length > 0) && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
                Capabilities
              </h4>
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
          )}

          {/* Sources */}
          {snapshot.activeSources.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
                Sources
              </h4>
              <ul className="text-xs space-y-0.5">
                {snapshot.activeSources.map((s, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className="text-[var(--color-text)]">{s.path}</span>
                    <span className="px-1.5 py-0 text-[10px] rounded bg-[var(--color-surface-alt)] text-[var(--color-text-muted)]">
                      {s.origin}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Activity */}
          {snapshot.whatAiDoing.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
                Activity
              </h4>
              <ul className="list-disc list-inside text-xs text-[var(--color-text)] space-y-0.5">
                {snapshot.whatAiDoing.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Key Summary */}
          {snapshot.keySummary.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mb-1">
                Summary
              </h4>
              <ul className="list-disc list-inside text-xs text-[var(--color-text)] space-y-0.5">
                {snapshot.keySummary.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
