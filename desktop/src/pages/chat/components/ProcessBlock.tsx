/**
 * ProcessBlock — one folded row for a segment of agent work.
 *
 * Renders a `process` unit from `deriveTurnView` (pages/chat/turnView.ts): the
 * tool calls, thinking and between-tool notes of one segment, collapsed into a
 * single quiet line. Three layers of disclosure:
 *   1. the row: "Working 1m09s · 10 steps · read 7 files" (always visible)
 *   2. the steps: verb + object per tool call, notes and thinking in time order
 *   3. the raw command / result of one step (click a step)
 * While streaming, the row stays folded but shows the last FINISHED step, the
 * CURRENT step (or "Thinking"), and the latest note or thought line — enough to
 * follow the work, never a wall of rows.
 *
 * Status comes ONLY from the projection (`ToolStep.status`); this component never
 * re-derives whether a tool is done.
 *
 * Design: no borders or background fills on rows — whitespace separates, one hover
 * tint marks interactivity (s_frontend-design design-judgment checks 2-4).
 *
 * @exports ProcessBlock
 */
import { useEffect, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { MarkdownRenderer } from '../../../components/common';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import type { ProcessUnit, Step, ToolStep, StepVerb } from '../turnView';
import { stepLabel, summarizeProcess } from '../turnView';
import { tf } from '../tf';

const VERB_FALLBACK: Record<StepVerb, [string, string]> = {
  read: ['Reading', 'Read'], edit: ['Editing', 'Edited'], search: ['Searching', 'Searched'],
  run: ['Running', 'Ran'], list: ['Listing', 'Listed'], fetch: ['Fetching', 'Fetched'],
  websearch: ['Searching web', 'Searched web'], loadTools: ['Loading tools', 'Loaded tools'],
  skill: ['Using skill', 'Used skill'], todo: ['Updating', 'Updated'], agent: ['Running agent', 'Ran agent'],
  mcp: ['Calling', 'Called'], tool: ['Running', 'Ran'],
};
const COUNT_FALLBACK: Partial<Record<StepVerb, [string, string]>> = {
  read: ['read {{count}} file', 'read {{count}} files'],
  edit: ['edited {{count}} file', 'edited {{count}} files'],
  search: ['{{count}} search', '{{count}} searches'],
  run: ['ran {{count}} command', 'ran {{count}} commands'],
  fetch: ['fetched {{count}} page', 'fetched {{count}} pages'],
};

const verbText = (t: TFunction, verb: StepVerb, live: boolean) =>
  tf(t, `chat.turn.verb.${verb}.${live ? 'live' : 'done'}`, VERB_FALLBACK[verb][live ? 0 : 1]);

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

/** Seconds since `startedAt`, ticking once a second only while `active`. */
function useTurnElapsed(startedAt: string | undefined, active: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  if (!active || !startedAt) return null;
  const start = Date.parse(startedAt);
  return Number.isFinite(start) ? Math.max(0, Math.floor((now - start) / 1000)) : null;
}

function openFile(path: string, e: React.SyntheticEvent) {
  e.preventDefault();
  e.stopPropagation();
  document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path } }));
}

function StatusIcon({ status }: { status: ToolStep['status'] }) {
  if (status === 'pending') {
    return <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)] animate-spin">progress_activity</span>;
  }
  if (status === 'error') {
    return <span className="material-symbols-outlined text-[14px] text-red-400">close</span>;
  }
  if (status === 'unknown') {
    // The turn ended on this call — stopped or cut off; never claim it finished.
    return <span className="material-symbols-outlined text-[14px] text-[var(--color-text-dim)]" title="Did not finish">stop_circle</span>;
  }
  if (status === 'waiting') {
    // Not run yet — gated by a permission / question shown right below.
    return <span className="material-symbols-outlined text-[14px] text-amber-400" title="Waiting for you">hourglass_empty</span>;
  }
  return <span className="material-symbols-outlined text-[14px] text-status-online opacity-70">check</span>;
}

function ToolStepRow({ step }: { step: ToolStep }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = stepLabel(step.block);
  const isError = step.status === 'error';
  const verb = label.verb === 'tool' ? '' : verbText(t, label.verb, step.status === 'pending' || step.status === 'waiting');
  // Two SIBLING controls (never a link nested in a button): the row toggles the
  // raw command; a file target is its own button that opens the file.
  return (
    <div data-testid="step-row" className={isError ? 'rounded-md bg-red-500/10' : undefined}>
      <div className="flex items-center gap-2.5 px-2 py-1 rounded-md text-[13px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors min-w-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title={tf(t, 'chat.turn.showCommand', 'Show command')}
          className={`flex items-center gap-2.5 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 ${label.path ? 'shrink-0' : 'flex-1 min-w-0'}`}
        >
          <StatusIcon status={step.status} />
          {/* An unrecognised summary is shown verbatim — no invented verb. */}
          {verb && <span className={isError ? 'text-red-400' : 'text-[var(--color-text-secondary)]'}>{verb}</span>}
          {!label.path && <span className="font-mono text-[12px] truncate" title={label.object}>{label.object}</span>}
        </button>
        {label.path && (
          <button
            type="button"
            onClick={(e) => openFile(label.path as string, e)}
            title={label.path}
            className="font-mono text-[12px] text-[var(--color-explorer-accent,var(--color-text-muted))] truncate min-w-0 hover:underline rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
          >
            {label.object}
          </button>
        )}
        {/* What the call returned, Kiro-style: "lines 1–240", "12 matches", the
            last output line, or the error. Absent for history (not persisted). */}
        {step.result?.outcome && (
          <span
            data-testid="step-outcome"
            title={step.result.outcome}
            className={`ml-auto pl-2 text-[12px] truncate min-w-0 max-w-[45%] ${isError ? 'text-red-400' : 'text-[var(--color-text-dim)]'}`}
          >
            {step.result.outcome}
          </span>
        )}
      </div>
      {open && (
        <pre className="mx-2 mb-1.5 ml-8 px-3 py-2 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] font-mono text-[11.5px] leading-relaxed text-[var(--color-text-muted)] whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
          {label.raw}
          {step.result?.content ? `\n\n${step.result.content}` : ''}
        </pre>
      )}
    </div>
  );
}

function StepRow({ step }: { step: Step }) {
  const { t } = useTranslation();
  if (step.kind === 'tool') return <ToolStepRow step={step} />;
  if (step.kind === 'thinking') {
    return (
      // Open by default: the user expanded the process to see it. Uncontrolled
      // after mount, so collapsing one thought survives streaming re-renders.
      <details open className="px-2 py-1 text-[13px] text-[var(--color-text-muted)]">
        <summary className="cursor-pointer select-none">{tf(t, 'chat.turn.thought', 'Thought')}</summary>
        <div className="mt-1 ml-1 italic text-[12px] whitespace-pre-wrap max-h-64 overflow-y-auto">{step.text}</div>
      </details>
    );
  }
  return (
    <div className="px-2 py-1 text-[13px] text-[var(--color-text-muted)] [&_p]:my-1">
      <MarkdownRenderer content={step.text} />
    </div>
  );
}

function LiveStep({ step }: { step: ToolStep }) {
  const { t } = useTranslation();
  const label = stepLabel(step.block);
  const elapsed = usePendingToolElapsed(step.block.id, true);
  return (
    <>
      <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)] animate-spin">progress_activity</span>
      {label.verb !== 'tool' && <span className="text-[var(--color-text-secondary)]">{verbText(t, label.verb, true)}</span>}
      <span className="font-mono text-[12px] truncate min-w-0" title={label.object}>{label.object}</span>
      {elapsed !== null && elapsed >= 3 && (
        <span className="ml-auto pl-2 text-[12px] text-[var(--color-text-dim)] tabular-nums shrink-0">{formatSeconds(elapsed)}</span>
      )}
    </>
  );
}

export interface ProcessBlockProps {
  unit: ProcessUnit;
  expanded: boolean;
  onToggle: () => void;
  /** ISO start time of the turn (message timestamp) — drives the live elapsed. */
  startedAt?: string;
}

/** Expanded lists show this many steps first; the rest are one click away. */
const STEP_PAGE = 12;

export function ProcessBlock({ unit, expanded, onToggle, startedAt }: ProcessBlockProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const summary = summarizeProcess(unit);
  const turnElapsed = useTurnElapsed(startedAt, unit.isLive);

  const facts: string[] = [];
  if (summary.stepCount > 0) {
    facts.push(tf(t, 'chat.turn.steps', summary.stepCount === 1 ? '{{count}} step' : '{{count}} steps', { count: summary.stepCount }));
  }
  for (const { verb, count } of summary.counts.slice(0, 2)) {
    const fb = COUNT_FALLBACK[verb];
    if (fb) facts.push(tf(t, `chat.turn.counts.${verb}`, count === 1 ? fb[0] : fb[1], { count }));
  }

  const hasThinking = unit.steps.some((s) => s.kind === 'thinking');
  const noteCount = unit.steps.filter((s) => s.kind === 'note').length;
  // A finished segment with no tool calls still needs words on its row.
  const restingLabel = summary.stepCount > 0
    ? null
    : hasThinking
      ? tf(t, 'chat.turn.thought', 'Thought')
      : tf(t, 'chat.turn.notes', noteCount === 1 ? '{{count}} note' : '{{count}} notes', { count: noteCount });
  const title = unit.isLive
    ? `${summary.stepCount === 0 && !unit.live?.note ? tf(t, 'chat.turn.thinking', 'Thinking') : tf(t, 'chat.turn.working', 'Working')}${turnElapsed !== null ? ` ${formatSeconds(turnElapsed)}` : ''}`
    : restingLabel;
  const canExpand = unit.steps.length > 0;
  // Nothing to expand yet (pre-content "Thinking") → a plain line, not a dead button.
  const Row = canExpand ? 'button' : 'div';
  // A LIVE segment pages from the END so the running step stays in view; a
  // finished one pages from the start (reading order).
  const visibleSteps = showAll
    ? unit.steps
    : unit.isLive ? unit.steps.slice(-STEP_PAGE) : unit.steps.slice(0, STEP_PAGE);
  const hiddenCount = unit.steps.length - visibleSteps.length;
  const showAllButton = hiddenCount > 0 && (
    <button
      type="button"
      data-testid="show-all-steps"
      onClick={() => setShowAll(true)}
      className="ml-2 my-0.5 px-1 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
    >
      {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: hiddenCount })}
    </button>
  );

  return (
    <div className="my-1" data-testid="process-block">
      <Row
        {...(canExpand ? { type: 'button' as const, onClick: onToggle, 'aria-expanded': expanded } : {})}
        data-testid="process-row"
        title={canExpand ? tf(t, expanded ? 'chat.turn.hideSteps' : 'chat.turn.showSteps', expanded ? 'Hide steps' : 'Show steps') : undefined}
        className={`-ml-2 flex items-center gap-2 px-2 py-1 rounded-md text-[13px] text-[var(--color-text-muted)] max-w-full transition-colors ${canExpand ? 'hover:bg-[var(--color-hover)] cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span
          className="material-symbols-outlined text-[16px] text-[var(--color-text-dim)] transition-transform duration-150"
          style={{ transform: expanded ? 'rotate(90deg)' : 'none', visibility: canExpand ? 'visible' : 'hidden' }}
        >
          chevron_right
        </span>
        {title && <span className="text-[var(--color-text-secondary)] font-medium tabular-nums">{title}</span>}
        {facts.map((f, i) => (
          <span key={f} className="flex items-center gap-2 tabular-nums">
            {(i > 0 || title) && <span className="text-[var(--color-text-dim)]">·</span>}
            <span className={i === 0 && !title ? 'text-[var(--color-text-secondary)]' : undefined}>{f}</span>
          </span>
        ))}
        {summary.errorCount > 0 && (
          <span className="flex items-center gap-2">
            <span className="text-[var(--color-text-dim)]">·</span>
            <span className="text-red-400">{tf(t, 'chat.turn.failed', '{{count}} failed', { count: summary.errorCount })}</span>
          </span>
        )}
      </Row>

      {unit.isLive && !expanded && (
        <div data-testid="process-live" className="ml-1.5 pl-4 border-l-2 border-[var(--color-border)]">
          {unit.live?.note && (
            <div className="py-0.5 text-[12.5px] leading-relaxed text-[var(--color-text-dim)] line-clamp-3 whitespace-pre-wrap">
              {unit.live.note}
            </div>
          )}
          {unit.live?.recent && (
            <div data-testid="process-live-recent" className="-ml-2">
              <ToolStepRow step={unit.live.recent} />
            </div>
          )}
          {unit.live?.thinking && (
            <div
              data-testid="process-live-thinking"
              title={unit.live.thinking}
              className="py-0.5 text-[12.5px] italic text-[var(--color-text-dim)] truncate"
            >
              {unit.live.thinking}
            </div>
          )}
          <div className="flex items-center gap-2.5 py-1 text-[13px] text-[var(--color-text-muted)] min-h-[28px] min-w-0">
            {unit.live?.step ? (
              <LiveStep step={unit.live.step} />
            ) : (
              // Between steps (a result just came back, the model is deciding) —
              // still visibly working; this replaced TabView's activity spinner.
              <>
                <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)] animate-spin">progress_activity</span>
                <span className="text-[var(--color-text-secondary)]">{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
              </>
            )}
          </div>
        </div>
      )}

      {!expanded && unit.unrecoveredFailure && (
        <div className="ml-1.5 pl-4 border-l-2 border-red-500/40">
          <ToolStepRow step={unit.unrecoveredFailure} />
        </div>
      )}

      {expanded && (
        <div className="ml-1.5 pl-3 border-l-2 border-[var(--color-border)] py-0.5">
          {unit.isLive && showAllButton}
          {visibleSteps.map((s) => (
            <StepRow key={s.kind === 'tool' ? `tu-${s.block.id}` : `${s.kind}-${s.index}`} step={s} />
          ))}
          {!unit.isLive && showAllButton}
        </div>
      )}
    </div>
  );
}
