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
  type BriefingTodo,
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

const PRIORITY_BORDER: Record<string, string> = {
  P0: 'border-l-red-400',
  P1: 'border-l-yellow-400',
  P2: 'border-l-blue-400',
};

function FocusItem({
  item,
  onClick,
  onDismiss,
}: {
  item: BriefingFocusItem;
  onClick?: (title: string) => void;
  onDismiss?: (title: string) => void;
}) {
  const badge = PRIORITY_BADGES[item.priority] ?? PRIORITY_BADGES.P2;
  const borderCls = PRIORITY_BORDER[item.priority] ?? PRIORITY_BORDER.P2;
  return (
    <div
      className={`border-l-2 ${borderCls} pl-2.5 py-1.5 group w-full text-left rounded-r px-2 -mx-1 transition-colors hover:bg-[var(--color-bg-hover)] flex items-center gap-1`}
    >
      <button
        type="button"
        onClick={() => onClick?.(item.title)}
        className="flex-1 min-w-0 cursor-pointer text-left"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border shrink-0 ${badge.cls}`}>
              {badge.label}
            </span>
            <span className="text-sm text-[var(--color-text)] truncate">{item.title}</span>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            {item.momentum && (
              <span className="text-[10px] bg-green-500/15 text-green-400 px-1.5 py-0.5 rounded font-mono" title="Momentum from last session">
                &#x26A1; active
              </span>
            )}
            <svg
              width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              className="text-[var(--color-text-secondary)] opacity-0 group-hover:opacity-100 transition-opacity"
              aria-hidden="true"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
          </div>
        </div>
      </button>
      {/* Dismiss button — appears on hover */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onDismiss?.(item.title);
        }}
        className="shrink-0 p-0.5 rounded opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:bg-[var(--color-bg-hover)] transition-all cursor-pointer"
        title="Dismiss this suggestion"
        aria-label={`Dismiss: ${item.title}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}

const URGENCY_BORDER: Record<string, string> = {
  high: 'border-l-red-400',
  medium: 'border-l-yellow-400',
  low: 'border-l-[var(--color-text-secondary)]',
};

function SignalItem({ signal, onAsk }: { signal: BriefingSignal; onAsk?: (text: string) => void }) {
  const borderCls = URGENCY_BORDER[signal.urgency] ?? URGENCY_BORDER.medium;
  const colorCls = URGENCY_COLORS[signal.urgency] ?? URGENCY_COLORS.medium;

  const handleAsk = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onAsk?.(`Tell me more about: ${signal.title}`);
  };

  return (
    <div className={`border-l-2 ${borderCls} pl-2.5 py-1.5 group hover:bg-[var(--color-bg-hover)] rounded-r px-2 -mx-1 transition-colors`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={handleAsk}
            className="text-sm text-[var(--color-text)] hover:underline text-left leading-snug cursor-pointer"
            title="Ask Swarm about this"
          >
            {signal.title}
          </button>
          {signal.summary && (
            <p className="text-[11px] text-[var(--color-text-secondary)] mt-0.5 line-clamp-2 leading-relaxed">
              {signal.summary}
            </p>
          )}
          <div className="flex items-center gap-2 mt-0.5">
            {signal.source && (
              <span className="text-[10px] text-[var(--color-text-secondary)] bg-[var(--color-bg-hover)] px-1.5 py-0.5 rounded">
                {signal.source}
              </span>
            )}
            <span className={`text-[10px] font-mono uppercase ${colorCls}`}>
              {signal.urgency}
            </span>
          </div>
        </div>
        {signal.url && (
          <a
            href={signal.url}
            className="shrink-0 mt-0.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text)] opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
            title="Open in browser"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              import('@tauri-apps/plugin-opener').then(({ openUrl }) => openUrl(signal.url!)).catch(() => window.open(signal.url!, '_blank', 'noopener,noreferrer'));
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          </a>
        )}
      </div>
    </div>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 1) return '<1s';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
}

const JOB_STATUS_BORDER: Record<string, string> = {
  success: 'border-l-green-400',
  failed: 'border-l-red-400',
  error: 'border-l-red-400',
};

function JobItem({ job }: { job: BriefingJob }) {
  const isSuccess = job.status === 'success';
  const hasFile = !!job.resultFile;
  const hasSummary = !!job.summary;
  const borderCls = JOB_STATUS_BORDER[job.status] ?? 'border-l-[var(--color-text-secondary)]';

  const handleClick = () => {
    if (hasFile) openWorkspaceFile(job.resultFile!);
  };

  return (
    <div
      role={hasFile ? 'button' : undefined}
      tabIndex={hasFile ? 0 : undefined}
      onClick={hasFile ? handleClick : undefined}
      onKeyDown={hasFile ? (e) => { if (e.key === 'Enter') handleClick(); } : undefined}
      className={`border-l-2 ${borderCls} pl-2.5 py-1.5 group rounded-r px-2 -mx-1 transition-colors ${hasFile ? 'hover:bg-[var(--color-bg-hover)] cursor-pointer' : ''}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm text-[var(--color-text)] truncate">
            {job.name}
          </span>
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${
            isSuccess
              ? 'bg-green-500/15 text-green-400'
              : 'bg-red-500/15 text-red-400'
          }`}>
            {isSuccess ? 'OK' : 'FAIL'}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {job.duration > 0 && (
            <span className="text-[10px] text-[var(--color-text-secondary)] font-mono">
              {formatDuration(job.duration)}
            </span>
          )}
          {hasFile && (
            <svg
              width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              className="text-[var(--color-text-secondary)] opacity-0 group-hover:opacity-100 transition-opacity"
              aria-label="Open result"
            >
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          )}
        </div>
      </div>
      {hasSummary && (
        <p className="text-[11px] text-[var(--color-text-secondary)] mt-0.5 line-clamp-2 leading-relaxed">
          {job.summary}
        </p>
      )}
    </div>
  );
}

const TODO_PRIORITY_BORDER: Record<string, string> = {
  high: 'border-l-red-400',
  medium: 'border-l-yellow-400',
  low: 'border-l-blue-400',
  none: 'border-l-[var(--color-text-secondary)]',
};

const TODO_PRIORITY_BADGE: Record<string, { label: string; cls: string }> = {
  high: { label: 'HIGH', cls: 'bg-red-500/15 text-red-400' },
  medium: { label: 'MED', cls: 'bg-yellow-500/15 text-yellow-400' },
  low: { label: 'LOW', cls: 'bg-blue-500/15 text-blue-400' },
  none: { label: '', cls: '' },
};

function TodoItem({ todo, onClick }: { todo: BriefingTodo; onClick?: (text: string) => void }) {
  const borderCls = TODO_PRIORITY_BORDER[todo.priority] ?? TODO_PRIORITY_BORDER.none;
  const badge = TODO_PRIORITY_BADGE[todo.priority] ?? TODO_PRIORITY_BADGE.none;
  const isOverdue = todo.status === 'overdue';

  return (
    <button
      type="button"
      onClick={() => onClick?.(`[ToDo:${todo.id}] ${todo.title}`)}
      className={`border-l-2 ${borderCls} pl-2.5 py-1.5 group w-full text-left rounded-r px-2 -mx-1 transition-colors hover:bg-[var(--color-bg-hover)] cursor-pointer`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm text-[var(--color-text)] truncate">{todo.title}</span>
          {badge.label && (
            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${badge.cls}`}>
              {badge.label}
            </span>
          )}
          {isOverdue && (
            <span className="text-[10px] bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded font-mono shrink-0">
              OVERDUE
            </span>
          )}
        </div>
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className="text-[var(--color-text-secondary)] opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
          aria-hidden="true"
        >
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </div>
      {todo.nextStep && (
        <p className="text-[11px] text-[var(--color-text-secondary)] mt-0.5 line-clamp-1 leading-relaxed">
          Next: {todo.nextStep}
        </p>
      )}
    </button>
  );
}

const SECTION_ICONS: Record<string, React.ReactNode> = {
  'Suggested Focus': (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" />
    </svg>
  ),
  'External Signals': (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  ),
  'Recent Jobs': (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  'Radar': (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><line x1="9" y1="9" x2="15" y2="9" /><line x1="9" y1="13" x2="15" y2="13" /><line x1="9" y1="17" x2="12" y2="17" />
    </svg>
  ),
};

function BriefingSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="text-left w-full">
      <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-text-secondary)] mb-2">
        <span className="opacity-60">{SECTION_ICONS[title]}</span>
        {title}
      </h3>
      {children}
    </div>
  );
}

const SIGNALS_COLLAPSED_COUNT = 3;

export interface WelcomeScreenProps {
  onFocusClick?: (title: string) => void;
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onFocusClick }) => {
  const [briefing, setBriefing] = useState<SessionBriefing | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [signalsExpanded, setSignalsExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    systemService.getBriefing()
      .then((data) => { if (!cancelled) setBriefing(data); })
      .catch(() => {})  // graceful — show fallback taglines on error
      .finally(() => { if (!cancelled) setLoaded(true); });
    return () => { cancelled = true; };
  }, []);

  const handleDismissFocus = (title: string) => {
    // Optimistic: remove from UI immediately
    if (briefing) {
      setBriefing({
        ...briefing,
        focus: briefing.focus.filter((f) => f.title !== title),
      });
    }
    // Persist server-side (fire-and-forget — already removed from UI)
    systemService.dismissFocus(title).catch(() => {});
  };

  const hasFocus = briefing && briefing.focus.length > 0;
  const hasSignals = briefing && briefing.signals.length > 0;
  const hasJobs = briefing && briefing.jobs.length > 0;
  const hasTodos = briefing && briefing.todos && briefing.todos.length > 0;
  const hasAnyBriefing = hasFocus || hasSignals || hasJobs || hasTodos || briefing?.learning;

  return (
    <div className="flex flex-col items-center h-full text-center px-4 overflow-y-auto">
      {/* Top spacer — pushes content to center when short, collapses when overflowing */}
      <div className="flex-1 min-h-6" />

      {/* Icon with gradient glow */}
      <div className="relative mb-4 select-none">
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
        className="text-2xl font-bold mb-2 select-none"
        style={{
          background: 'linear-gradient(135deg, #00d4ff 0%, #a855f7 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
        }}
      >
        Welcome to SwarmAI
      </h1>

      {/* Briefing panel, fallback, or nothing while loading */}
      {!loaded ? null : hasAnyBriefing ? (
        <div className="w-full max-w-lg mt-5 divide-y divide-[var(--color-border)] [&>*]:py-4 [&>*:first-child]:pt-0 [&>*:last-child]:pb-0">
          {/* Focus suggestions */}
          {hasFocus && (
            <div>
              <BriefingSection title="Suggested Focus">
                <div className="space-y-0.5">
                  {briefing!.focus.map((item, i) => (
                    <FocusItem key={i} item={item} onClick={onFocusClick} onDismiss={handleDismissFocus} />
                  ))}
                </div>
              </BriefingSection>
            </div>
          )}

          {/* External signals */}
          {hasSignals && (
            <div>
              <BriefingSection title="External Signals">
                <div className="space-y-1">
                  {(signalsExpanded
                    ? briefing!.signals
                    : briefing!.signals.slice(0, SIGNALS_COLLAPSED_COUNT)
                  ).map((sig, i) => (
                    <SignalItem key={i} signal={sig} onAsk={onFocusClick} />
                  ))}
                </div>
                {briefing!.signals.length > SIGNALS_COLLAPSED_COUNT && (
                  <button
                    type="button"
                    onClick={() => setSignalsExpanded((prev) => !prev)}
                    className="text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text)] mt-1.5 cursor-pointer transition-colors"
                  >
                    {signalsExpanded
                      ? '▴ Show less'
                      : `▾ ${briefing!.signals.length - SIGNALS_COLLAPSED_COUNT} more signals`}
                  </button>
                )}
              </BriefingSection>
            </div>
          )}

          {/* Job results */}
          {hasJobs && (
            <div>
              <BriefingSection title="Recent Jobs">
                <div className="space-y-0.5">
                  {briefing!.jobs.map((job, i) => (
                    <JobItem key={i} job={job} />
                  ))}
                </div>
              </BriefingSection>
            </div>
          )}

          {/* Radar todos */}
          {hasTodos && (
            <div>
              <BriefingSection title="Radar">
                <div className="space-y-0.5">
                  {briefing!.todos.map((todo, i) => (
                    <TodoItem key={i} todo={todo} onClick={onFocusClick} />
                  ))}
                </div>
              </BriefingSection>
            </div>
          )}

          {/* Learning insight */}
          {briefing?.learning && (
            <div className="text-left">
              <div className="flex items-start gap-2 bg-[var(--color-bg-hover)] rounded-md px-3 py-2">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-purple-400 shrink-0 mt-0.5">
                  <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                  <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                </svg>
                <p className="text-[11px] text-[var(--color-text-secondary)] leading-relaxed">
                  {briefing.learning}
                </p>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          <p className="text-base text-[var(--color-text)]">
            Work smarter. Move faster. Stress less.
          </p>
          <p className="text-sm text-[var(--color-text-secondary)] max-w-sm">
            Remembers everything. Learns every session. Gets better every time.
          </p>
          <div className="flex items-center gap-4 mt-4 text-[var(--color-text-secondary)]">
            <div className="flex items-center gap-1.5 text-[11px]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-60">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              Chat
            </div>
            <div className="flex items-center gap-1.5 text-[11px]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-60">
                <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              Research
            </div>
            <div className="flex items-center gap-1.5 text-[11px]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-60">
                <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
              </svg>
              Code
            </div>
            <div className="flex items-center gap-1.5 text-[11px]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-60">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
              </svg>
              Remember
            </div>
          </div>
        </div>
      )}

      {/* Bottom spacer — mirrors top spacer for vertical centering */}
      <div className="flex-1 min-h-6" />
    </div>
  );
};

export default WelcomeScreen;
