/**
 * WelcomeScreen — branded landing view with live session briefing.
 *
 * Displays a centered welcome screen with:
 * - Circular SwarmAI brand icon with radial gradient glow
 * - "Welcome to SwarmAI!" heading with gradient text
 * - Live session briefing: focus suggestions, external signals, job results
 * - Falls back to taglines when no briefing data is available
 *
 * The briefing data comes from GET /api/system/briefing which reads
 * MEMORY.md open threads, signal_digest.json, and job results.
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

function FocusItem({ item }: { item: BriefingFocusItem }) {
  const badge = PRIORITY_BADGES[item.priority] ?? PRIORITY_BADGES.P2;
  return (
    <div className="flex items-center gap-2 py-1">
      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${badge.cls}`}>
        {badge.label}
      </span>
      <span className="text-sm text-[var(--color-text)] truncate">{item.title}</span>
      {item.momentum && (
        <span className="text-[10px] text-green-400 whitespace-nowrap" title="Has momentum from last session">
          &#x26A1;
        </span>
      )}
    </div>
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
  return (
    <div className="flex items-center gap-2 py-0.5 text-sm">
      <span className={isSuccess ? 'text-green-400' : 'text-red-400'}>
        {isSuccess ? '\u2713' : '\u2717'}
      </span>
      <span className="text-[var(--color-text-secondary)] truncate">{job.name}</span>
      {job.duration > 0 && (
        <span className="text-[11px] text-[var(--color-text-secondary)]">
          {job.duration < 60 ? `${Math.round(job.duration)}s` : `${Math.round(job.duration / 60)}m`}
        </span>
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

export const WelcomeScreen: React.FC = () => {
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
                <FocusItem key={i} item={item} />
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
