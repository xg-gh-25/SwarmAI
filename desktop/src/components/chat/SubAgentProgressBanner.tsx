/**
 * SubAgentProgressBanner — tiered awareness UI for long-running sub-agents.
 *
 * Renders a contextual banner below the streaming cursor when an Agent tool
 * has been running for >60s. Tiers escalate from subtle timer (T1) to
 * orange/red warnings (T3/T4) with action guidance.
 *
 * Does NOT force-kill agents. Informs the user so they can decide.
 */

import React from 'react';
import type { SubAgentProgress } from '../../hooks/useSubAgentProgress';

interface SubAgentProgressBannerProps {
  progress: SubAgentProgress;
}

/** Format seconds as "M:SS" */
function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export const SubAgentProgressBanner: React.FC<SubAgentProgressBannerProps> = ({ progress }) => {
  if (!progress.active || progress.tier === 0) {
    return null;
  }

  const elapsed = formatElapsed(progress.elapsedS);
  const label = progress.label
    ? progress.label.length > 60
      ? progress.label.slice(0, 57) + '...'
      : progress.label
    : 'Sub-agent';

  // Tier 1: Subtle elapsed timer
  if (progress.tier === 1) {
    return (
      <div className="flex items-center gap-2 mt-2 text-xs text-[var(--color-text-muted)]">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
        <span className="font-mono">{elapsed}</span>
        <span className="truncate max-w-[200px]">{label}</span>
      </div>
    );
  }

  // Tier 2: Yellow notice
  if (progress.tier === 2) {
    return (
      <div className="mt-2 px-3 py-1.5 rounded-md bg-yellow-500/10 border border-yellow-500/20 text-xs text-yellow-700 dark:text-yellow-300">
        <div className="flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-500 animate-pulse" />
          <span className="font-mono font-medium">{elapsed}</span>
          <span className="truncate">{label}</span>
        </div>
        <p className="mt-0.5 text-[var(--color-text-muted)]">
          Running 3+ min — normal for research/design tasks.
        </p>
      </div>
    );
  }

  // Tier 3: Orange warning
  if (progress.tier === 3) {
    return (
      <div className="mt-2 px-3 py-1.5 rounded-md bg-orange-500/10 border border-orange-500/20 text-xs text-orange-700 dark:text-orange-300">
        <div className="flex items-center gap-2">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-orange-500 animate-pulse" />
          <span className="font-mono font-medium">{elapsed}</span>
          <span className="truncate">{label}</span>
        </div>
        <p className="mt-0.5 text-[var(--color-text-muted)]">
          8+ minutes. May be doing extensive work or stuck. Use Stop if needed.
        </p>
      </div>
    );
  }

  // Tier 4: Red soft ceiling
  return (
    <div className="mt-2 px-3 py-2 rounded-md bg-red-500/10 border border-red-500/20 text-xs text-red-700 dark:text-red-300">
      <div className="flex items-center gap-2">
        <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
        <span className="font-mono font-medium">{elapsed}</span>
        <span className="truncate">{label}</span>
      </div>
      <p className="mt-1 text-[var(--color-text-muted)]">
        15+ min without response. Consider stopping if not making progress.
      </p>
    </div>
  );
};
