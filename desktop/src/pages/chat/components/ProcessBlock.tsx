/**
 * ProcessBlock — one work block: ADJACENT thinking + tool calls, as one card.
 *
 * Renders a `process` unit from `deriveTurnView` (pages/chat/turnView.ts). The
 * chat reads 思考 → 动作 → 回复 in stream order: text the agent writes between tool
 * calls is a separate `note` unit in the main stream, never a step in here.
 *
 * Disclosure (chat display v6, XG 2026-10-17):
 *   - header: "Worked · 10 steps · read 7 files · 2 thoughts · 1 failed"
 *   - the LIVE block is open, a fixed-height list that follows the newest step
 *   - a finished block folds 2s after the next item arrives (`useLinger`), unless
 *     the user opened or closed it — that choice is kept
 *   - a thinking step streams open while it is the newest item and folds to one
 *     "Thought" line 2s after something follows it ("不然这个会让用户等待焦虑")
 *   - adjacent finished reads of one file are one row (`groupSteps`)
 *   - one step's raw command / result opens on click
 *
 * Status comes ONLY from the projection (`ToolStep.status`); this component never
 * re-derives whether a tool is done.
 *
 * @exports ProcessBlock
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { useLinger } from '../../../hooks/useLinger';
import type { ProcessUnit, StepRowItem, ThinkingStep, ToolStep, StepVerb } from '../turnView';
import { groupSteps, stepLabel, summarizeProcess } from '../turnView';
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

const Spinner = () => (
  <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)] animate-spin">progress_activity</span>
);

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
  if (status === 'pending') return <Spinner />;
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

/** One tool call — or, with `merged`, adjacent finished reads of one file. */
function ToolStepRow({ step, merged }: { step: ToolStep; merged?: ToolStep[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = stepLabel(step.block);
  const isError = step.status === 'error';
  const isPending = step.status === 'pending';
  const elapsed = usePendingToolElapsed(step.block.id, isPending);
  const verb = label.verb === 'tool' ? '' : verbText(t, label.verb, isPending || step.status === 'waiting');
  const all = merged ?? [step];
  // What the call returned, Kiro-style: "lines 1–240", "12 matches", the last
  // output line, or the error. Merged reads list each range once.
  const outcome = [...new Set(all.map((s) => s.result?.outcome).filter((o): o is string => !!o))].join(', ');
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
        {all.length > 1 && (
          <span data-testid="step-repeat" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">
            {tf(t, 'chat.turn.readTimes', '{{count}}×', { count: all.length })}
          </span>
        )}
        {outcome && (
          <span
            data-testid="step-outcome"
            title={outcome}
            className={`ml-auto pl-2 text-[12px] truncate min-w-0 max-w-[45%] ${isError ? 'text-red-400' : 'text-[var(--color-text-dim)]'}`}
          >
            {outcome}
          </span>
        )}
        {isPending && elapsed !== null && elapsed >= 3 && (
          <span className="ml-auto pl-2 text-[12px] text-[var(--color-text-dim)] tabular-nums shrink-0">{formatSeconds(elapsed)}</span>
        )}
      </div>
      {open && (
        <pre className="mx-2 mb-1.5 ml-8 px-3 py-2 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] font-mono text-[11.5px] leading-relaxed text-[var(--color-text-muted)] whitespace-pre-wrap break-all max-h-64 overflow-y-auto">
          {all.map((s) => `${stepLabel(s.block).raw}${s.result?.content ? `\n\n${s.result.content}` : ''}`).join('\n\n')}
        </pre>
      )}
    </div>
  );
}

/**
 * A thinking passage. While it is the newest item of the live block it is open
 * and streams; 2s after anything follows it, it folds to one "Thought" line. A
 * click is the user's choice and wins over both.
 */
function ThinkingRow({ step, streaming }: { step: ThinkingStep; streaming: boolean }) {
  const { t } = useTranslation();
  const auto = useLinger(streaming);
  const [choice, setChoice] = useState<boolean | undefined>(undefined);
  const open = choice ?? auto;
  const preview = step.text.split('\n').map((l) => l.trim()).find(Boolean) ?? '';
  return (
    <div data-testid="thinking-row" data-open={open ? 'true' : 'false'} className="px-2 py-1 text-[13px] text-[var(--color-text-muted)]">
      <button
        type="button"
        onClick={() => setChoice(!open)}
        aria-expanded={open}
        className="flex items-center gap-2.5 w-full min-w-0 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        {streaming ? <Spinner /> : (
          <span className="material-symbols-outlined text-[14px] text-[var(--color-text-dim)]">psychology</span>
        )}
        <span className="text-[var(--color-text-secondary)] shrink-0">
          {streaming ? tf(t, 'chat.turn.thinking', 'Thinking') : tf(t, 'chat.turn.thought', 'Thought')}
        </span>
        {!open && preview && <span className="italic text-[12px] text-[var(--color-text-dim)] truncate min-w-0">{preview}</span>}
      </button>
      {open && (
        <div
          data-testid="thinking-text"
          className={`mt-1 ml-6 italic text-[12.5px] leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap ${streaming ? '' : 'max-h-64 overflow-y-auto'}`}
        >
          {step.text}
        </div>
      )}
    </div>
  );
}

/** The running step on the folded live row (only when the user folded it). */
function LiveStep({ step }: { step: ToolStep }) {
  const { t } = useTranslation();
  const label = stepLabel(step.block);
  const elapsed = usePendingToolElapsed(step.block.id, true);
  return (
    <>
      <Spinner />
      {label.verb !== 'tool' && <span className="text-[var(--color-text-secondary)]">{verbText(t, label.verb, true)}</span>}
      <span className="font-mono text-[12px] truncate min-w-0" title={label.object}>{label.object}</span>
      {elapsed !== null && elapsed >= 3 && (
        <span className="ml-auto pl-2 text-[12px] text-[var(--color-text-dim)] tabular-nums shrink-0">{formatSeconds(elapsed)}</span>
      )}
    </>
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
  /** ISO start time of the turn (message timestamp) — drives the live elapsed. */
  startedAt?: string;
}

/** Expanded lists show this many steps first; the rest are one click away. */
const STEP_PAGE = 12;
/** The live list follows the bottom unless the user scrolled this far up. */
const STICK_PX = 24;

export function ProcessBlock({ unit, expanded, onToggle, startedAt }: ProcessBlockProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const summary = summarizeProcess(unit);
  const turnElapsed = useTurnElapsed(startedAt, unit.isLive);
  const autoOpen = useLinger(unit.isLive);
  const canExpand = unit.steps.length > 0;
  const open = canExpand && (expanded ?? autoOpen);

  const facts: string[] = [];
  if (summary.stepCount > 0) {
    facts.push(tf(t, 'chat.turn.steps', summary.stepCount === 1 ? '{{count}} step' : '{{count}} steps', { count: summary.stepCount }));
    for (const { verb, count } of summary.counts.slice(0, 2)) {
      const fb = COUNT_FALLBACK[verb];
      if (fb) facts.push(tf(t, `chat.turn.counts.${verb}`, count === 1 ? fb[0] : fb[1], { count }));
    }
    if (summary.thinkingCount > 0) {
      const n = summary.thinkingCount;
      facts.push(tf(t, 'chat.turn.thoughtCount', n === 1 ? '{{count}} thought' : '{{count}} thoughts', { count: n }));
    }
  }
  const title = unit.isLive
    ? `${summary.stepCount === 0 ? tf(t, 'chat.turn.thinking', 'Thinking') : tf(t, 'chat.turn.working', 'Working')}${turnElapsed !== null ? ` ${formatSeconds(turnElapsed)}` : ''}`
    : summary.stepCount > 0 ? tf(t, 'chat.turn.worked', 'Worked') : tf(t, 'chat.turn.thought', 'Thought');

  // Merging and paging are pure functions of the steps — memoized per block.
  const rows = useMemo(() => {
    const grouped = groupSteps(unit.steps);
    const keys = rowKeys(grouped);
    return grouped.map((row, i) => ({ row, key: keys[i] }));
  }, [unit.steps]);
  // A LIVE block pages from the END so the running step stays in view; a
  // finished one pages from the start (reading order).
  const visibleRows = showAll ? rows : unit.isLive ? rows.slice(-STEP_PAGE) : rows.slice(0, STEP_PAGE);
  const hiddenCount = rows.length - visibleRows.length;
  const newest = unit.steps[unit.steps.length - 1];
  const pending = unit.live?.step;
  // Between steps (a result came back, the model is deciding): still visibly busy.
  const idle = unit.isLive && !pending && newest?.kind !== 'thinking';

  // Follow the newest step while live, unless the user scrolled up to read.
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const tail = newest?.kind === 'thinking' ? newest.text.length : newest?.kind === 'tool' ? newest.status : '';
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
  const Row = canExpand ? 'button' : 'div';

  return (
    <div
      data-testid="process-block"
      data-live={unit.isLive ? 'true' : 'false'}
      className="my-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)]"
    >
      <Row
        {...(canExpand ? { type: 'button' as const, onClick: () => onToggle(!open), 'aria-expanded': open } : {})}
        data-testid="process-row"
        title={canExpand ? tf(t, open ? 'chat.turn.hideSteps' : 'chat.turn.showSteps', open ? 'Hide steps' : 'Show steps') : undefined}
        className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-left text-[13px] text-[var(--color-text-muted)] min-w-0 transition-colors ${canExpand ? 'hover:bg-[var(--color-hover)] cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        {unit.isLive ? <Spinner /> : (
          <span
            className="material-symbols-outlined text-[16px] text-[var(--color-text-dim)] transition-transform duration-150"
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          >
            chevron_right
          </span>
        )}
        <span className="text-[var(--color-text-secondary)] font-medium tabular-nums shrink-0">{title}</span>
        {facts.map((f) => (
          <span key={f} className="flex items-center gap-2 tabular-nums shrink-0">
            <span className="text-[var(--color-text-dim)]">·</span>
            <span>{f}</span>
          </span>
        ))}
        {summary.errorCount > 0 && (
          <span className="flex items-center gap-2 shrink-0">
            <span className="text-[var(--color-text-dim)]">·</span>
            <span className="text-red-400">{tf(t, 'chat.turn.failed', '{{count}} failed', { count: summary.errorCount })}</span>
          </span>
        )}
        {canExpand && unit.isLive && (
          <span
            className="ml-auto material-symbols-outlined text-[16px] text-[var(--color-text-dim)]"
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          >
            chevron_right
          </span>
        )}
      </Row>

      {open && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          data-testid="process-steps"
          className={`border-t border-[var(--color-border)] px-1.5 py-1 ${unit.isLive ? 'h-[300px] overflow-y-auto' : ''}`}
        >
          {unit.isLive && showAllButton}
          {visibleRows.map(({ row, key }) => (row.kind === 'reads'
            ? <ToolStepRow key={key} step={row.steps[0]} merged={row.steps} />
            : row.step.kind === 'tool'
              ? <ToolStepRow key={key} step={row.step} />
              : <ThinkingRow key={key} step={row.step} streaming={unit.isLive && row.step === newest} />
          ))}
          {idle && (
            <div data-testid="process-live-idle" className="flex items-center gap-2.5 px-2 py-1 text-[13px] text-[var(--color-text-muted)]">
              <Spinner />
              <span className="text-[var(--color-text-secondary)]">{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
            </div>
          )}
          {!unit.isLive && showAllButton}
        </div>
      )}

      {/* The user folded the live block: still say what was just done and what runs now. */}
      {unit.isLive && canExpand && !open && (
        <div data-testid="process-live" className="border-t border-[var(--color-border)] px-1.5 py-1">
          {unit.live?.recent && (
            <div data-testid="process-live-recent">
              <ToolStepRow step={unit.live.recent} />
            </div>
          )}
          {unit.live?.thinking && (
            <div
              data-testid="process-live-thinking"
              title={unit.live.thinking}
              className="px-2 py-0.5 text-[12.5px] italic text-[var(--color-text-dim)] truncate"
            >
              {unit.live.thinking}
            </div>
          )}
          <div className="flex items-center gap-2.5 px-2 py-1 text-[13px] text-[var(--color-text-muted)] min-h-[28px] min-w-0">
            {pending ? <LiveStep step={pending} /> : (
              <>
                <Spinner />
                <span className="text-[var(--color-text-secondary)]">{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* A failure the turn never recovered from stays visible on the folded card. */}
      {!open && unit.unrecoveredFailure && (
        <div className="border-t border-[var(--color-border)] px-1.5 py-1">
          <ToolStepRow step={unit.unrecoveredFailure} />
        </div>
      )}
    </div>
  );
}
