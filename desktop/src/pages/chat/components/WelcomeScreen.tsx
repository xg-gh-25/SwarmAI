/**
 * WelcomeScreen — branded landing view with live session briefing.
 *
 * Displays a centered welcome screen with:
 * - Circular SwarmAI brand icon with radial gradient glow
 * - "Welcome to SwarmAI!" heading with gradient text
 * - Live session briefing: focus suggestions, external signals, job results
 * - Falls back to taglines when no briefing data is available
 *
 * Interactive behaviors:
 * - Focus items are clickable — sends the title as a chat message
 * - Signal items open external URLs in browser
 * - Job items show summary; clicking opens the result file in editor
 *
 * @exports WelcomeScreen
 */

import React, { useEffect, useState } from 'react';
import {
  systemService,
  type SessionBriefing,
  type BriefingFocusItem,
  type BriefingSignal,
  type BriefingJob,
} from '../../../services/system';

const URGENCY_COLORS: Record<string, string> = {
  high: 'text-red-400',
  medium: 'text-yellow-400',
  low: 'text-[var(--color-text-secondary)]',
};

const PRIORITY_BADGES: Record<string, { label: string; cls: string }> = {
  P0: { label: 'P0', cls: 'bg-red-500/20 text-red-400 border-red-500/30' },
  P1: { label: 'P1', cls: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30' },
  P2: { label: 'P2', cls: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
};

function openWorkspaceFile(relativePath: string) {
  document.dispatchEvent(
    new CustomEvent('swarm:open-file', { detail: { path: relativePath } }),
  );
}

function FocusItem({ item, onClick }: { item: BriefingFocusItem; onClick?: (title: string) => void }) {
  const badge = PRIORITY_BADGES[item.priority] ?? PRIORITY_BADGES.P2;
  return (
    <button
      type="button"
      onClick={() => onClick?.(item.title)}
      className="flex items-center gap-2 py-1 w-full text-left rounded px-1 -mx-1 transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer"
    >
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${badge.cls}`}>
        {badge.label}
      </span>
      <span className="text-sm text-[var(--color-text)] truncate">{item.title}</span>
      {item.momentum && (
        <span className="text-[10px] text-green-400 whitespace-nowrap shrink-0" title="Has momentum from last session">
          &#x26A1;
        </span>
      )}
    </button>
  );
}

function SignalItem({ signal }: { signal: BriefingSignal }) {
  const colorCls = URGENCY_COLORS[signal.urgency] ?? URGENCY_COLORS.medium;
  return (
    <a
      href={signal.url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-start gap-2 py-1 group hover:bg-[var(--color-bg-hover)] rounded px-1 -mx-1 transition-colors"
    >
      <span className={`text-[10px] font-mono mt-0.5 uppercase ${colorCls}`}>
        {signal.urgency}
      </span>
      <div className="min-w-0">
        <span className="text-sm text-[var(--color-text)] group-hover:underline truncate block">
          {signal.title}
        </span>
        {signal.source && (
          <span className="text-[11px] text-[var(--color-text-secondary)]">{signal.source}</span>
        )}
      </div>
    </a>
  );
}

function JobItem({ job }: { job: BriefingJob }) {
  const isSuccess = job.status === 'success';
  const hasFile = !!job.resultFile;
  const hasSummary = !!job.summary;

  const handleClick = () => {
    if (hasFile) openWorkspaceFile(job.resultFile!);
  };

  return (
    <div
      role={hasFile ? 'button' : undefined}
      tabIndex={hasFile ? 0 : undefined}
      onClick={hasFile ? handleClick : undefined}
      onKeyDown={hasFile ? (e) => { if (e.key === 'Enter') handleClick(); } : undefined}
      className={`py-1.5 rounded px-1 -mx-1 transition-colors ${hasFile ? 'hover:bg-[var(--color-bg-hover)] cursor-pointer' : ''}`}
    >
      <div className="flex items-center gap-2 text-sm">
        <span className={isSuccess ? 'text-green-400 shrink-0' : 'text-red-400 shrink-0'}>
          {isSuccess ? '\u2713' : '\u2717'}
        </span>
        <span className={`truncate ${hasFile ? 'text-[var(--color-text)] hover:underline' : 'text-[var(--color-text-secondary)]'}`}>
          {job.name}
        </span>
      </div>
      {hasSummary && (
        <p className="text-[11px] text-[var(--color-text-secondary)] mt-0.5 ml-5 line-clamp-2 leading-relaxed">
          {job.summary}
        </p>
      )}
    </div>
  );
}

function BriefingSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="text-left w-full">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-secondary)] mb-1.5">
        {title}
      </h3>
      {children}
    </div>
  );
}

export interface WelcomeScreenProps {
  onFocusClick?: (title: string) => void;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onFocusClick }) => {
  const [briefing, setBriefing] = useState<SessionBriefing | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    systemService.getBriefing().then((data) => {
      if (!cancelled) {
        setBriefing(data);
        setLoaded(true);
      }
    });
    return () => { cancelled = true; };
  }, []);

  const hasFocus = briefing && briefing.focus.length > 0;
  const hasSignals = briefing && briefing.signals.length > 0;
  const hasJobs = briefing && briefing.jobs.length > 0;
  const hasAnyBriefing = hasFocus || hasSignals || hasJobs || briefing?.learning;

  return (
    <div className="flex flex-col items-center justify-center h-full text-center select-none px-4">
      {/* Icon with gradient glow */}
      <div className="relative mb-4">
        <div
          className="absolute top-1/2 left-1/2 w-[120px] h-[120px] -translate-x-1/2 -translate-y-1/2 rounded-full pointer-events-none"
          style={{
            background:
              'radial-gradient(circle, rgba(0, 212, 255, 0.2) 0%, transparent 70%)',
          }}
          aria-hidden="true"
        />
        <img
          src="/swarm-avatar.svg"
          alt="SwarmAI icon"
          className="relative w-14 h-14 rounded-full"
          draggable={false}
        />
      </div>

      {/* Heading with gradient text */}
      <h1
        className="text-2xl font-bold mb-2"
        style={{
          background: 'linear-gradient(135deg, #00d4ff 0%, #a855f7 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
        }}
      >
        Welcome to SwarmAI
      </h1>

      {/* Briefing panel or fallback taglines */}
      {loaded && hasAnyBriefing ? (
        <div className="w-full max-w-md mt-4 space-y-4">
          {/* Focus suggestions */}
          {hasFocus && (
            <BriefingSection title="Suggested Focus">
              {briefing!.focus.map((item, i) => (
                <FocusItem key={i} item={item} onClick={onFocusClick} />
              ))}
            </BriefingSection>
          )}

          {/* External signals */}
          {hasSignals && (
            <BriefingSection title="External Signals">
              {briefing!.signals.slice(0, 3).map((sig, i) => (
                <SignalItem key={i} signal={sig} />
              ))}
            </BriefingSection>
          )}

          {/* Job results */}
          {hasJobs && (
            <BriefingSection title="Recent Jobs">
              {briefing!.jobs.map((job, i) => (
                <JobItem key={i} job={job} />
              ))}
            </BriefingSection>
          )}

          {/* Learning insight */}
          {briefing?.learning && (
            <div className="text-left text-[11px] text-[var(--color-text-secondary)] italic">
              {briefing.learning}
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Fallback taglines when no briefing data */}
          <p className="text-base text-[var(--color-text)] mb-1">
            Work smarter. Move faster. Stress less.
          </p>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Remembers everything. Learns every session. Gets better every time.
          </p>
        </>
      )}
    </div>
  );
};

export default WelcomeScreen;
