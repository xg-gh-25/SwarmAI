/**
 * ProcessBlock — one run of ADJACENT thinking + tool calls (chat display v7.1).
 *
 * Renders a `process` unit from `deriveTurnView` (pages/chat/turnView.ts). Weight
 * follows the user's current question, not the data volume (design-judgment
 * Surface 6 — "attention moves across the turn"):
 *   - FINISHED work is demoted to one dim line that says its EFFECT: the files it
 *     changed (FileChips, click → diff), or the one step it was, or what it mostly
 *     did. No border, no fill; it opens to its steps on click ("process folds, it
 *     is not deleted").
 *   - The LIVE block is the one thing that stands out: a neutral card titled by
 *     what is running now ("正在运行 pytest …") with that step's elapsed — the
 *     screen's only spinner and only timer. Below it, a fixed-height list that
 *     follows the newest step; the current thought streams there.
 *   - Colour only for exceptions: failures red, waiting amber; done is neutral.
 *   - A finished block folds 2s after the next item arrives (`useLinger`); a thought
 *     folds 2s after something follows it; a user's open/closed choice wins.
 *   - Adjacent finished reads of one file are one row; an Agent step nests its
 *     sub-agent's steps (`AgentDetail`); streamed text wraps, never one replaced line.
 *
 * Status comes ONLY from the projection (`ToolStep.status`).
 *
 * @exports ProcessBlock
 */
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { useLinger } from '../../../hooks/useLinger';
import type { ProcessUnit, StepRowItem, ThinkingStep, ToolStep, StepVerb } from '../turnView';
import { STEP_VERB_FALLBACK, changedFiles, groupSteps, stepLabel, summarizeProcess } from '../turnView';
import { tf } from '../tf';
import { FileChip } from './FileChip';

const VERB_FALLBACK = STEP_VERB_FALLBACK;
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

const Spinner = () => (
  <span className="material-symbols-outlined text-[14px] text-[var(--color-text-secondary)] animate-spin shrink-0">progress_activity</span>
);

/** Done is neutral; only an exception (failed / waiting) carries colour. A still
 *  pending step in a LIST is a static dot — the live title holds the one spinner. */
function StatusIcon({ status }: { status: ToolStep['status'] }) {
  const base = 'material-symbols-outlined text-[13px] leading-none shrink-0 w-[14px] text-center';
  if (status === 'pending') return <span className={`${base} text-[var(--color-text-muted)]`}>more_horiz</span>;
  if (status === 'error') return <span className={`${base} text-red-400`}>close</span>;
  if (status === 'unknown') {
    // The turn ended on this call — stopped or cut off; never claim it finished.
    return <span className={`${base} text-[var(--color-text-dim)]`} title="Did not finish">stop_circle</span>;
  }
  if (status === 'waiting') {
    return <span className={`${base} text-amber-400`} title="Waiting for you">hourglass_empty</span>;
  }
  return <span className={`${base} text-[var(--color-text-dim)]`}>check</span>;
}

/** A step's object: the file as a chip (an edit opens its diff), else the command. */
function StepObject({ step, wrap }: { step: ToolStep; wrap?: boolean }) {
  const label = stepLabel(step.block);
  if (label.path) return <FileChip path={label.path} name={label.object} diff={label.verb === 'edit'} />;
  return (
    <span className={`font-mono text-[12px] min-w-0 ${wrap ? 'break-all' : 'truncate'}`} title={label.object}>
      {label.object}
    </span>
  );
}

/** An outcome up to this long stays on its step's row; longer ones wrap below. */
const INLINE_OUTCOME_CHARS = 40;

/** One tool call — or, with `merged`, adjacent finished reads of one file. */
function ToolStepRow({ step, merged }: { step: ToolStep; merged?: ToolStep[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = stepLabel(step.block);
  const isError = step.status === 'error';
  const verb = label.verb === 'tool' ? '' : verbText(t, label.verb, step.status === 'pending' || step.status === 'waiting');
  const all = merged ?? [step];
  // What the call returned: "lines 1–240", "12 matches", the last output line, the error.
  const outcome = [...new Set(all.map((s) => s.result?.outcome).filter((o): o is string => !!o))].join(', ');
  const inlineOutcome = !!outcome && !isError && outcome.length <= INLINE_OUTCOME_CHARS;
  const outcomeTone = isError ? 'text-red-400' : 'text-[var(--color-text-dim)]';
  return (
    <div data-testid="step-row" data-status={step.status}>
      <div className="flex items-center gap-2 px-2 py-0.5 rounded-md text-[12.5px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors min-w-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title={tf(t, 'chat.turn.showCommand', 'Show command')}
          className="flex items-center gap-2 shrink-0 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <StatusIcon status={step.status} />
          {/* An unrecognised summary is shown verbatim — no invented verb. */}
          {verb && <span className={isError ? 'text-red-400' : 'text-[var(--color-text-secondary)]'}>{verb}</span>}
        </button>
        <StepObject step={step} />
        {all.length > 1 && (
          <span data-testid="step-repeat" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">
            {tf(t, 'chat.turn.readTimes', '{{count}}×', { count: all.length })}
          </span>
        )}
        {inlineOutcome && (
          <span data-testid="step-outcome" className={`ml-auto pl-2 text-[12px] shrink-0 ${outcomeTone}`}>{outcome}</span>
        )}
      </div>
      {outcome && !inlineOutcome && (
        <div
          data-testid="step-outcome"
          className={`ml-8 mr-2 mb-0.5 text-[12px] leading-relaxed whitespace-pre-wrap break-words ${outcomeTone}`}
        >
          {outcome}
        </div>
      )}
      {(step.children?.length || step.progress) && <AgentDetail step={step} />}
      {open && (
        <pre className="mx-2 mb-1.5 ml-8 px-3 py-2 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] font-mono text-[11.5px] leading-relaxed text-[var(--color-text-muted)] whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
          {all.map((s) => `${stepLabel(s.block).raw}${s.result?.content ? `\n\n${s.result.content}` : ''}`).join('\n\n')}
        </pre>
      )}
    </div>
  );
}

/**
 * A thinking passage: streams open while it is the newest item of the live block
 * (text only — the live title already says "Thinking"), folds to one line 2s after
 * anything follows it. A click is the user's choice and wins over both.
 */
function ThinkingRow({ step, streaming }: { step: ThinkingStep; streaming: boolean }) {
  const { t } = useTranslation();
  const auto = useLinger(streaming);
  const [choice, setChoice] = useState<boolean | undefined>(undefined);
  const open = choice ?? auto;
  const preview = step.text.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  return (
    <div data-testid="thinking-row" data-open={open ? 'true' : 'false'} className="pl-6 pr-2 py-0.5 text-[12px] text-[var(--color-text-muted)]">
      {!streaming && (
        <button
          type="button"
          onClick={() => setChoice(!open)}
          aria-expanded={open}
          className="flex items-center gap-1.5 w-full min-w-0 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span className="text-[var(--color-text-dim)] shrink-0">{tf(t, 'chat.turn.thought', 'Thought')}</span>
          {!open && preview && <span className="italic text-[var(--color-text-dim)] truncate min-w-0">{preview}</span>}
        </button>
      )}
      {open && (
        <div
          data-testid="thinking-text"
          className={`italic leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap ${streaming ? '' : 'mt-0.5 max-h-48 overflow-y-auto'}`}
        >
          {step.text}
        </div>
      )}
    </div>
  );
}

/** A RUNNING sub-agent shows its newest steps, this many. */
const AGENT_LIVE_ROWS = 6;

/**
 * What a sub-agent (an Agent step) is doing, nested under that step. While it runs:
 * open, its newest steps and the tool it is on (the Agent row's own elapsed is the
 * one timer). Once done it folds (2s later) to "Sub-agent · N steps", plus its
 * summary or failure. A click is the user's choice and wins.
 */
function AgentDetail({ step }: { step: ToolStep }) {
  const { t } = useTranslation();
  const subs = useMemo(() => step.children ?? [], [step.children]);
  const p = step.progress;
  const running = step.status === 'pending';
  const auto = useLinger(running);
  const [choice, setChoice] = useState<boolean | undefined>(undefined);
  const open = subs.length > 0 && (choice ?? auto);
  const rows = useMemo(() => groupSteps(subs), [subs]);
  const shown = open ? (running && choice === undefined ? rows.slice(-AGENT_LIVE_ROWS) : rows) : [];
  const earlier = open ? rows.length - shown.length : 0;
  const count = Math.max(subs.length, p?.toolUses ?? 0);
  const failed = p?.status === 'failed' || p?.status === 'stopped';

  const facts: string[] = [];
  if (count > 0) facts.push(tf(t, 'chat.turn.steps', count === 1 ? '{{count}} step' : '{{count}} steps', { count }));
  if (!running && p?.durationMs) facts.push(formatSeconds(Math.round(p.durationMs / 1000)));
  if (running && p?.lastToolName && !subs.length) {
    facts.push(tf(t, 'chat.turn.nowUsing', 'now {{tool}}', { tool: p.lastToolName }));
  }
  const Head = subs.length > 0 ? 'button' : 'div';
  return (
    <div data-testid="agent-detail" className="ml-8 mr-2 mb-1">
      <Head
        {...(subs.length > 0 ? { type: 'button' as const, onClick: () => setChoice(!open), 'aria-expanded': open } : {})}
        className="flex items-center gap-1.5 py-0.5 text-[12px] text-[var(--color-text-dim)] text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        <span className="material-symbols-outlined text-[14px]" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>chevron_right</span>
        <span className="text-[var(--color-text-secondary)]">{tf(t, 'chat.turn.subAgent', 'Sub-agent')}</span>
        {facts.map((f) => <span key={f} className="tabular-nums">· {f}</span>)}
        {failed && <span className="text-red-400">· {tf(t, `chat.turn.subAgentStatus.${p?.status}`, p?.status ?? '')}</span>}
      </Head>
      {open && (
        <div data-testid="agent-steps" className="ml-1.5 pl-2 border-l border-[var(--color-border)]">
          {earlier > 0 && (
            <button
              type="button"
              onClick={() => setChoice(true)}
              className="px-2 py-0.5 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded"
            >
              {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: earlier })}
            </button>
          )}
          {shown.map((r) => (r.kind === 'reads'
            ? <ToolStepRow key={`tu-${r.steps[0].block.id}`} step={r.steps[0]} merged={r.steps} />
            : r.step.kind === 'tool' ? <ToolStepRow key={`tu-${r.step.block.id}`} step={r.step} /> : null
          ))}
        </div>
      )}
      {!running && p?.summary && (
        <div data-testid="agent-summary" className="py-0.5 text-[12px] leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap break-words">
          {p.summary}
        </div>
      )}
    </div>
  );
}

/** Tool steps key by tool_use id. Thinking keys by its ordinal among the block's
 *  thoughts: block indices shift when the stream-end reconcile drops live-only
 *  tool_result blocks, and a remount would drop the user's open/closed choice. */
function rowKeys(rows: StepRowItem[]): string[] {
  let thought = 0;
  return rows.map((r) => {
    const s = r.kind === 'reads' ? r.steps[0] : r.step;
    if (s.kind === 'tool') return `tu-${s.block.id}`;
    thought += 1;
    return `th-${thought}`;
  });
}

export interface ProcessBlockProps {
  unit: ProcessUnit;
  /** The user's open/closed choice for this block; undefined = automatic. */
  expanded?: boolean;
  onToggle: (open: boolean) => void;
}

/** Expanded lists show this many steps first; the rest are one click away. */
const STEP_PAGE = 12;
/** The live list follows the bottom unless the user scrolled this far up. */
const STICK_PX = 24;
/** The folded line names at most this many changed files. */
const MAX_LINE_FILES = 3;

/** The folded line of FINISHED work: its effect, in one dim line. */
function FoldedSummary({ unit }: { unit: ProcessUnit }) {
  const { t } = useTranslation();
  const summary = summarizeProcess(unit);
  const tools = unit.steps.filter((s): s is ToolStep => s.kind === 'tool');
  const failed = summary.errorCount > 0 && (
    <span className={`shrink-0 ${unit.unrecoveredFailure ? 'text-red-400' : 'text-[var(--color-text-dim)]'}`}>
      · {unit.unrecoveredFailure
        ? tf(t, 'chat.turn.failed', '{{count}} failed', { count: summary.errorCount })
        : tf(t, 'chat.turn.failedRecovered', '{{count}} failed, recovered', { count: summary.errorCount })}
    </span>
  );
  if (!tools.length) {
    const first = unit.steps.find((s): s is ThinkingStep => s.kind === 'thinking');
    const preview = first?.text.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
    return (
      <>
        <span className="text-[var(--color-text-secondary)] shrink-0">{tf(t, 'chat.turn.thought', 'Thought')}</span>
        {preview && <span className="italic text-[var(--color-text-dim)] truncate min-w-0">{preview}</span>}
      </>
    );
  }
  if (tools.length === 1) {
    const s = tools[0];
    const label = stepLabel(s.block);
    const oc = s.result?.outcome;
    return (
      <>
        {label.verb !== 'tool' && <span className="text-[var(--color-text-secondary)] shrink-0">{verbText(t, label.verb, false)}</span>}
        <StepObject step={s} />
        {oc && s.status !== 'error' && <span className="text-[var(--color-text-dim)] truncate min-w-0">· {oc}</span>}
        {s.status === 'error' && <span className="text-red-400 truncate min-w-0">· {oc || tf(t, 'chat.turn.failedOne', 'failed')}</span>}
      </>
    );
  }
  const files = changedFiles(unit.steps);
  const facts: string[] = [];
  if (!files.length) {
    for (const { verb, count } of summary.counts.slice(0, 2)) {
      const fb = COUNT_FALLBACK[verb];
      if (fb) facts.push(tf(t, `chat.turn.counts.${verb}`, count === 1 ? fb[0] : fb[1], { count }));
    }
  }
  return (
    <>
      <span className="text-[var(--color-text-secondary)] shrink-0">{tf(t, 'chat.turn.worked', 'Worked')}</span>
      {files.length > 0 && (
        <span className="flex items-center gap-1 min-w-0 overflow-hidden">
          <span className="shrink-0">{tf(t, 'chat.turn.changed', 'changed')}</span>
          {files.slice(0, MAX_LINE_FILES).map((p) => <FileChip key={p} path={p} diff />)}
          {files.length > MAX_LINE_FILES && (
            <span className="shrink-0">{tf(t, 'chat.turn.andMore', '+{{count}}', { count: files.length - MAX_LINE_FILES })}</span>
          )}
        </span>
      )}
      {facts.map((f) => <span key={f} className="shrink-0">· {f}</span>)}
      <span className="shrink-0 text-[var(--color-text-dim)] tabular-nums">
        · {tf(t, 'chat.turn.steps', '{{count}} steps', { count: summary.stepCount })}
      </span>
      {failed}
    </>
  );
}

/** The LIVE title: what runs now and how long it has — the one spinner, one timer. */
function LiveTitle({ pending }: { pending?: ToolStep }) {
  const { t } = useTranslation();
  const elapsed = usePendingToolElapsed(pending?.block.id ?? '', !!pending);
  if (!pending) {
    return (
      <>
        <Spinner />
        <span className="text-[var(--color-text)] font-medium">{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
      </>
    );
  }
  const label = stepLabel(pending.block);
  return (
    <>
      <Spinner />
      {label.verb !== 'tool' && <span className="text-[var(--color-text)] font-medium shrink-0">{verbText(t, label.verb, true)}</span>}
      <StepObject step={pending} wrap />
      {elapsed !== null && elapsed >= 3 && (
        <span data-testid="live-elapsed" className="ml-auto pl-2 text-[12px] text-[var(--color-text-muted)] tabular-nums shrink-0">
          {formatSeconds(elapsed)}
        </span>
      )}
    </>
  );
}

export function ProcessBlock({ unit, expanded, onToggle }: ProcessBlockProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const autoOpen = useLinger(unit.isLive);
  const pending = unit.live?.step;
  // The running step is the title; the list holds everything else (one fact, once).
  const listSteps = useMemo(
    () => (pending ? unit.steps.filter((s) => s !== pending) : unit.steps),
    [unit.steps, pending],
  );
  const canExpand = listSteps.length > 0;
  const open = canExpand && (expanded ?? autoOpen);

  const rows = useMemo(() => {
    const grouped = groupSteps(listSteps);
    const keys = rowKeys(grouped);
    return grouped.map((row, i) => ({ row, key: keys[i] }));
  }, [listSteps]);
  // A LIVE block pages from the END so the newest step stays in view.
  const visibleRows = showAll ? rows : unit.isLive ? rows.slice(-STEP_PAGE) : rows.slice(0, STEP_PAGE);
  const hiddenCount = rows.length - visibleRows.length;
  const newest = unit.steps[unit.steps.length - 1];

  // Follow the newest step while live, unless the user scrolled up to read.
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const tail = newest?.kind === 'thinking'
    ? newest.text.length
    : newest?.kind === 'tool' ? `${newest.status}:${newest.children?.length ?? 0}` : '';
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && unit.isLive && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [unit.isLive, open, unit.steps.length, tail]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
  };

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
  const list = open && (
    <div
      ref={scrollRef}
      onScroll={onScroll}
      data-testid="process-steps"
      className={unit.isLive
        ? 'mt-1 max-h-[300px] overflow-y-auto'
        : 'ml-[7px] mt-0.5 mb-1 pl-2 border-l border-[var(--color-border)]'}
    >
      {unit.isLive && showAllButton}
      {visibleRows.map(({ row, key }) => (row.kind === 'reads'
        ? <ToolStepRow key={key} step={row.steps[0]} merged={row.steps} />
        : row.step.kind === 'tool'
          ? <ToolStepRow key={key} step={row.step} />
          : <ThinkingRow key={key} step={row.step} streaming={unit.isLive && row.step === newest} />
      ))}
      {!unit.isLive && showAllButton}
    </div>
  );
  const Row = canExpand ? 'button' : 'div';
  const rowProps = canExpand
    ? {
        type: 'button' as const,
        onClick: () => onToggle(!open),
        'aria-expanded': open,
        title: tf(t, open ? 'chat.turn.hideSteps' : 'chat.turn.showSteps', open ? 'Hide steps' : 'Show steps'),
      }
    : {};

  if (unit.isLive) {
    // The one thing that stands out while working: neutral emphasis, no hue.
    return (
      <div
        data-testid="process-block"
        data-live="true"
        className="my-2 border-l-2 border-[var(--color-text-muted)] bg-[var(--color-hover)]/40 rounded-r-lg px-2.5 py-1.5"
      >
        <Row
          {...rowProps}
          data-testid="process-row"
          className={`w-full flex items-center gap-2 text-left text-[13.5px] min-w-0 rounded ${canExpand ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
        >
          <LiveTitle pending={pending} />
          {canExpand && (
            <span
              className="material-symbols-outlined text-[16px] text-[var(--color-text-dim)] shrink-0 ml-1"
              style={{ transform: open ? 'rotate(90deg)' : 'none' }}
            >
              chevron_right
            </span>
          )}
        </Row>
        {pending && (pending.children?.length || pending.progress) ? <AgentDetail step={pending} /> : null}
        {list}
      </div>
    );
  }

  return (
    <div data-testid="process-block" data-live="false" className="my-0.5">
      <Row
        {...rowProps}
        data-testid="process-row"
        className={`-ml-1 w-full flex items-center gap-1.5 px-1 py-0.5 rounded-md text-left text-[12.5px] text-[var(--color-text-muted)] min-w-0 ${canExpand ? 'hover:bg-[var(--color-hover)] cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span
          className="material-symbols-outlined text-[15px] text-[var(--color-text-dim)] transition-transform duration-150 shrink-0"
          style={{ transform: open ? 'rotate(90deg)' : 'none', visibility: canExpand ? 'visible' : 'hidden' }}
        >
          chevron_right
        </span>
        <FoldedSummary unit={unit} />
      </Row>
      {/* A failure the turn never recovered from stays visible on the folded line. */}
      {!open && unit.unrecoveredFailure && (
        <div className="ml-[7px] pl-2 border-l border-red-500/40">
          <ToolStepRow step={unit.unrecoveredFailure} />
        </div>
      )}
      {list}
    </div>
  );
}
