/**
 * WorkGroup — one WORKING / WORKED block of a turn (chat display v7.12).
 *
 * Renders a `group` from `groupWork` (pages/chat/turnView.ts): EVERYTHING between two
 * first-level items of a turn (the intent, a stage row, an interrupt, the result).
 * A turn has two levels (XG 2026-10-18):
 *   - first level: INTENT · WORKING / WORKED · pipeline stage rows · RESULT
 *   - second level, inside a block, in stream order: thoughts, notes the model wrote
 *     between tool calls, landmarks and tool calls
 * So:
 *   - A turn with no result: `tail` (its last note) shows under the folded line, like
 *     an unrecovered failure; opened, it is in its place in the list. `defaultOpen`
 *     opens the turn's last block when there is no text to show at all.
 *   - LIVE (the last block of a streaming turn): an open WORKING card, no toggle. A
 *     running tool call carries the spinner and its elapsed; with nothing running (the
 *     model is thinking) the header holds the one spinner.
 *   - FINISHED: folded to one WORKED line of counts — steps · files changed · commands.
 *     No file names, no title borrowed from a thought (it would repeat the first line of
 *     the body). Opened, it lists its items in order. It folds FOLD_DELAY_MS after it
 *     stops being live (`useLinger`); a user's open/closed choice wins.
 *   - `flat` (inside a pipeline stage): the stage row is the fold, so the items render
 *     directly, no WORKING / WORKED header of their own.
 *
 * ToolStrip — one tool call, one row, a step quieter than the thoughts around it (XG
 * 2026-10-19): the REAL tool name (muted) and the REAL command or arguments (dim,
 * 12px; `toolCall`). A Bash command loses only its leading cd / export / setup and a
 * perl-alarm / timeout wrapper; nothing is translated. A file name is never cut (the
 * chip wraps). Adjacent reads of one file are one row (N×, with the ranges read). A
 * failure is one line below. The last line a command printed is never shown as a
 * "result".
 *
 * An Agent row shows its whole task description (wrapping) and no verdict. It is a
 * button: opened, it shows the task, its result and the sub-agent's own calls. It
 * says its elapsed while it runs and its duration once it finished.
 *
 * Density: two or more consecutive finished calls (not failed, not Agent) fold into
 * ONE row — "Bash ×4 · Read ×2" — that opens to one row per call. So a block reads
 * as what is running now plus a few folded runs. Nothing changes shape the moment
 * the next item arrives: a finished call joins the fold above, and a thought
 * collapses to one line, only FOLD_DELAY_MS after a later item arrived (`useSettled`,
 * the same 2s linger as every other fold). A live block cuts nothing off its top.
 * Status comes ONLY from the projection (`ToolStep.status`); colour only for exceptions.
 *
 * @exports WorkGroupView, ToolStrip, workCounts, pathTail
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { FOLD_DELAY_MS, useLinger } from '../../../hooks/useLinger';
import type { StepRowItem, ThoughtUnit, ToolStep, WorkGroup, WorkMember } from '../turnView';
import { changedFiles, groupSteps, stepLabel, toolCall } from '../turnView';
import { tf } from '../tf';
import { FileChip } from './FileChip';
import { ThoughtBlock } from './ThoughtBlock';
import { ANCHOR_PROPS } from '../scrollFocus';

/** A list (live: the newest; opened: the first) shows this many items before "show all". */
const PAGE = 12;

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

const Spinner = ({ size = 13 }: { size?: number }) => (
  <span aria-hidden="true" className="material-symbols-outlined animate-spin shrink-0 text-[var(--color-text-secondary)]" style={{ fontSize: size }}>progress_activity</span>
);

function StatusIcon({ status }: { status: ToolStep['status'] }) {
  const base = 'material-symbols-outlined text-[13px] leading-none shrink-0 w-[14px] text-center';
  if (status === 'pending') return <Spinner />;
  if (status === 'error') return <span className={`${base} text-red-400`}>close</span>;
  if (status === 'unknown') {
    // The turn ended on this call — stopped or cut off; never claim it finished.
    return <span className={`${base} text-[var(--color-text-dim)]`} title="Did not finish">stop_circle</span>;
  }
  if (status === 'waiting') return <span className={`${base} text-amber-400`} title="Waiting for you">hourglass_empty</span>;
  return <span className={`${base} text-[var(--color-text-dim)]`}>check</span>;
}

/** A long path keeps its last segments so the file name stays visible; the chip's
 *  tooltip and link keep the full path. */
const TAIL_SEGMENTS = 3;
export function pathTail(p: string): string {
  const parts = p.split('/');
  return parts.length > TAIL_SEGMENTS + 1 ? `…/${parts.slice(-TAIL_SEGMENTS).join('/')}` : p;
}

/** A sub-agent's detail lists at most this many of its own calls. */
const AGENT_DETAIL_CALLS = 12;
/** A sub-agent's result shown in its detail is capped at this many characters. */
const AGENT_RESULT_MAX = 600;

/** One tool call — or, with `merged`, adjacent finished reads of one file. */
export function ToolStrip({ step, merged }: { step: ToolStep; merged?: ToolStep[] }) {
  const { t } = useTranslation();
  const [agentOpen, setAgentOpen] = useState(false);
  const [allCalls, setAllCalls] = useState(false);
  const call = toolCall(step.block);
  const running = step.status === 'pending';
  const elapsed = usePendingToolElapsed(step.block.id, running);
  const isError = step.status === 'error';
  const all = merged ?? [step];
  // A merged read keeps the ranges it read — the arguments of those calls.
  const ranges = all.length > 1
    ? [...new Set(all.map((s) => s.result?.outcome).filter((o): o is string => !!o))].join(', ')
    : '';
  const errorText = isError ? step.result?.outcome ?? '' : '';
  // A sub-agent: its live progress while it runs; its result is in its detail.
  const verb = stepLabel(step.block).verb;
  const isAgent = verb === 'agent';
  const p = step.progress;
  const subCount = Math.max(step.children?.length ?? 0, p?.toolUses ?? 0);
  const agentFacts: string[] = [];
  if (isAgent) {
    if (subCount > 0) agentFacts.push(tf(t, 'chat.turn.steps', subCount === 1 ? '{{count}} step' : '{{count}} steps', { count: subCount }));
    if (running && p?.lastToolName) agentFacts.push(tf(t, 'chat.turn.nowUsing', 'now {{tool}}', { tool: p.lastToolName }));
  }
  const agentResult = isAgent ? (p?.summary || step.result?.outcome || '').trim() : '';
  const agentFailed = p?.status === 'failed' || p?.status === 'stopped';
  const durationMs = isAgent && !running ? p?.durationMs : undefined;
  const target = call.path
    ? <FileChip path={call.path} name={pathTail(call.text)} diff={verb === 'edit'} wrap />
    : call.text !== call.tool && (
      <span
        data-testid="strip-target"
        className={isAgent
          ? 'min-w-0 break-words text-[var(--color-text-muted)]'
          : 'truncate min-w-0 font-mono text-[11.5px] text-[var(--color-text-dim)]'}
        title={call.text}
      >
        {call.text}
      </span>
    );
  const Row = isAgent ? 'button' : 'div';
  return (
    <div data-testid="step-row" data-status={step.status} data-tool={call.tool} {...ANCHOR_PROPS}>
      <Row
        {...(isAgent ? {
          type: 'button' as const, 'data-testid': 'agent-toggle', 'aria-expanded': agentOpen,
          onClick: () => setAgentOpen((o) => !o),
        } : {})}
        className={`w-full flex ${isAgent ? 'items-start text-left cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : 'items-center'} gap-2 px-1.5 py-[3px] rounded-md text-[12px] text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] min-w-0`}
      >
        <span data-testid="strip-tool" title={call.tool} className={`shrink-0 min-w-[44px] max-w-[40%] truncate ${isError ? 'text-red-400' : 'text-[var(--color-text-muted)]'}`}>
          {call.tool}
        </span>
        {target}
        {all.length > 1 && (
          <span data-testid="step-repeat" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">
            {tf(t, 'chat.turn.readTimes', '{{count}}×', { count: all.length })}
          </span>
        )}
        {ranges && <span data-testid="step-ranges" className="truncate min-w-0 text-[12px] text-[var(--color-text-dim)]">{ranges}</span>}
        {agentFacts.length > 0 && (
          <span data-testid="agent-facts" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">· {agentFacts.join(' · ')}</span>
        )}
        {agentFailed && <span className="shrink-0 text-[12px] text-red-400">· {tf(t, `chat.turn.subAgentStatus.${p?.status}`, p?.status ?? '')}</span>}
        <span className="ml-auto shrink-0 flex items-center gap-1.5 pl-2">
          {running && elapsed !== null && elapsed >= 1 && (
            <span data-testid="live-elapsed" className="text-[12px] text-[var(--color-text-muted)] tabular-nums">{formatSeconds(elapsed)}</span>
          )}
          {typeof durationMs === 'number' && durationMs >= 0 && (
            <span data-testid="agent-duration" className="text-[12px] text-[var(--color-text-dim)] tabular-nums">{formatSeconds(Math.round(durationMs / 1000))}</span>
          )}
          <StatusIcon status={step.status} />
        </span>
      </Row>
      {errorText && (
        <div data-testid="step-error" className="ml-[60px] mr-2 mb-0.5 truncate text-[12px] text-red-400" title={errorText}>{errorText}</div>
      )}
      {isAgent && agentOpen && (
        <div data-testid="agent-detail" className="ml-[60px] mr-2 mb-1 pl-2 border-l border-[var(--color-border)] space-y-1 text-[12px] text-[var(--color-text-muted)]">
          <div className="break-words text-[var(--color-text-secondary)]">{call.text}</div>
          {subCount > 0 && (
            <div className="text-[var(--color-text-dim)] tabular-nums">
              {tf(t, 'chat.turn.steps', subCount === 1 ? '{{count}} step' : '{{count}} steps', { count: subCount })}
            </div>
          )}
          {agentResult && (
            <div data-testid="agent-result" className="whitespace-pre-wrap break-words">
              {agentResult.length > AGENT_RESULT_MAX ? `${agentResult.slice(0, AGENT_RESULT_MAX - 1)}…` : agentResult}
            </div>
          )}
          {(step.children?.length ?? 0) > 0 && (() => {
            // While it runs, its newest calls (what it is doing now); once done, its first.
            const all = step.children!;
            const shown = allCalls ? all : running ? all.slice(-AGENT_DETAIL_CALLS) : all.slice(0, AGENT_DETAIL_CALLS);
            const hidden = all.length - shown.length;
            const more = hidden > 0 && (
              <button
                type="button"
                data-testid="agent-calls-all"
                onClick={() => setAllCalls(true)}
                className="px-1.5 text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
              >
                {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: hidden })}
              </button>
            );
            return (
              <div className="space-y-0.5">
                {running && more}
                {foldRuns(groupSteps(shown)).map((it) => ('fold' in it ? <FoldStrip key={it.key} rows={it.fold} /> : stripOf(it.row)))}
                {!running && more}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

/** A run of folded finished calls: "Bash ×4 · Read ×2", opening to one row per call. */
function FoldStrip({ rows }: { rows: StepRowItem[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const counts = new Map<string, number>();
  for (const r of rows) {
    const steps = r.kind === 'reads' ? r.steps : [r.step];
    const tool = toolCall(steps[0].block).tool;
    counts.set(tool, (counts.get(tool) ?? 0) + steps.length);
  }
  const label = [...counts].map(([tool, n]) => tf(t, 'chat.turn.foldCount', '{{tool}} ×{{count}}', { tool, count: n })).join(' · ');
  return (
    <div data-testid="step-fold" {...ANCHOR_PROPS}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1 px-1.5 py-[3px] rounded-md text-left text-[12px] text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text-muted)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        <span aria-hidden="true" className="material-symbols-outlined text-[14px] shrink-0 transition-transform duration-150" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>chevron_right</span>
        <span className="tabular-nums">{label}</span>
        <span className="ml-auto shrink-0"><StatusIcon status="done" /></span>
      </button>
      {open && <div className="ml-[7px] pl-1 border-l border-[var(--color-border)] space-y-0.5">{rows.map(stripOf)}</div>}
    </div>
  );
}

/** Rows key by tool_use id: stable across the stream-end reconcile. */
const rowKey = (r: StepRowItem) => `tu-${(r.kind === 'reads' ? r.steps[0] : r.step).block.id}`;
const stripOf = (r: StepRowItem) => (r.kind === 'reads'
  ? <ToolStrip key={rowKey(r)} step={r.steps[0]} merged={r.steps} />
  : <ToolStrip key={rowKey(r)} step={r.step} />);

type Item =
  | { key: string; thought: ThoughtUnit }
  | { key: string; note: Exclude<WorkMember, ThoughtUnit | { kind: 'process' }> }
  | { key: string; row: StepRowItem }
  | { key: string; fold: StepRowItem[] };

/** A row that may fold with its neighbours: finished, not failed, not a sub-agent —
 *  and SETTLED (`settled` null = all are). */
const foldable = (r: StepRowItem, settled: Set<string> | null = null): boolean =>
  (settled === null || settled.has(rowKey(r)))
  && (r.kind === 'reads' || (r.step.status === 'done' && stepLabel(r.step.block).verb !== 'agent'));

/** A block's members as one ordered list of thoughts, notes and tool rows, in stream
 *  order. Text members key by their text ordinal, which survives the stream-end
 *  reconcile and a note turning into a landmark. */
function rawItems(g: WorkGroup): Item[] {
  const raw: Item[] = [];
  for (const m of g.members) {
    if (m.kind === 'thought') raw.push({ key: `th-${m.ordinal}`, thought: m });
    else if (m.kind === 'process') for (const r of groupSteps(m.steps)) raw.push({ key: rowKey(r), row: r });
    else raw.push({ key: `tx-${m.textOrdinal}`, note: m });
  }
  return raw;
}

/**
 * Keys of the items that have SETTLED: the item is DONE (a call finished, a thought
 * stopped streaming) and a later item exists, and both have held for FOLD_DELAY_MS.
 * Nothing changes shape the moment the next item arrives or a call finishes: only a
 * settled call joins a fold, only a settled thought collapses to one line — the same
 * 2s linger every other fold uses. Items already done and followed when the block
 * mounts (a reload, a tab switch) are settled. Returns null when `active` is false: a
 * block that is no longer live has nothing pending, so everything is settled.
 */
function useSettled(items: Array<{ key: string; done: boolean }>, active: boolean): Set<string> | null {
  const followedAt = useRef<Map<string, number> | null>(null);
  const [tick, setTick] = useState(0);
  const now = Date.now();
  const followed = items.slice(0, -1).filter((it) => it.done).map((it) => it.key);
  if (followedAt.current === null) {
    followedAt.current = new Map(followed.map((k) => [k, -Infinity]));
  } else {
    for (const k of followed) if (!followedAt.current.has(k)) followedAt.current.set(k, now);
  }
  const map = followedAt.current;
  const pending = followed.map((k) => map.get(k) ?? -Infinity).filter((at) => at + FOLD_DELAY_MS > now);
  const nextAt = pending.length ? Math.min(...pending) + FOLD_DELAY_MS : null;
  useEffect(() => {
    if (!active || nextAt === null) return undefined;
    const id = setTimeout(() => setTick((n) => n + 1), Math.max(0, nextAt - Date.now()) + 1);
    return () => clearTimeout(id);
  }, [active, nextAt, tick]); // `tick`: re-arm if a timer fired early (a clock jump)
  if (!active) return null;
  return new Set(followed.filter((k) => (map.get(k) ?? Infinity) + FOLD_DELAY_MS <= now));
}

/** A thought that stopped streaming; a call (row) that finished; a note. */
const itemDone = (it: Item): boolean => ('thought' in it ? !it.thought.live
  : 'row' in it ? (it.row.kind === 'reads' || it.row.step.status !== 'pending') : true);

/** Two or more consecutive foldable rows → one fold item, keyed by its first row (so
 *  it stays the same element while later calls join it); everything else unchanged. */
function foldItems(items: Item[], settled: Set<string> | null = null): Item[] {
  const out: Item[] = [];
  let run: StepRowItem[] = [];
  const flushRun = () => {
    if (run.length >= 2) out.push({ key: `fold-${rowKey(run[0])}`, fold: run });
    else if (run.length === 1) out.push({ key: rowKey(run[0]), row: run[0] });
    run = [];
  };
  for (const it of items) {
    if ('row' in it && foldable(it.row, settled)) { run.push(it.row); continue; }
    flushRun();
    out.push(it);
  }
  flushRun();
  return out;
}

/** Tool rows only (a sub-agent's own calls), folded the same way. */
const foldRuns = (rows: StepRowItem[]) =>
  foldItems(rows.map((r) => ({ key: rowKey(r), row: r }))) as Array<Extract<Item, { row: StepRowItem } | { fold: StepRowItem[] }>>;

const toolSteps = (g: WorkGroup): ToolStep[] =>
  g.members.flatMap((m) => (m.kind === 'process' ? m.steps.filter((s): s is ToolStep => s.kind === 'tool') : []));

/** The folded line's counts: tool calls, files they changed, shell commands. */
export function workCounts(g: WorkGroup): { steps: number; files: number; commands: number; thoughts: number } {
  const steps = toolSteps(g);
  return {
    steps: steps.length,
    files: changedFiles(steps).length,
    commands: steps.filter((s) => s.block.name === 'Bash').length,
    thoughts: g.members.filter((m) => m.kind === 'thought').length,
  };
}

export interface WorkGroupViewProps {
  group: WorkGroup;
  /** The turn is over: thoughts clamp to one line. */
  compact?: boolean;
  /** The user's open/closed choice for this block; undefined = automatic. */
  expanded?: boolean;
  /** Inside a pipeline stage: the stage row is the fold, so the items render directly. */
  flat?: boolean;
  onToggle: (open: boolean) => void;
  /** The user's open choice for a thought (keyed `th-<ordinal>`). */
  thoughtOpen: (key: string) => boolean | undefined;
  onThoughtToggle: (key: string, open: boolean) => void;
  /** Renders a note / landmark's markdown (the message's own block renderer). */
  renderNote: (text: string, key: string) => ReactNode;
  /** The turn has no result and this block holds its last note: shown under the
   *  folded line (the turn's last words stay visible). */
  tail?: WorkMember;
  /** The turn has no result and no note to show: this, its last block, starts open. */
  defaultOpen?: boolean;
}

/** The WORKED / WORKING header's tag style — pipeline stage rows use the same. */
export const GROUP_TAG_CLASS = 'shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.08em]';
/** The icon slot before a WORKED tag (its chevron) and before a stage label (its
 *  status mark): one width, so the two labels start at the same x. */
export const ROW_ICON_SLOT = 'w-6 shrink-0 text-center';
const TAG = GROUP_TAG_CLASS;
/** The turn's last note under a folded line: readable, a step down from the answer. */
const TAIL_CLASS = 'ml-[7px] pl-2 mt-0.5 text-[13.5px] [&_p]:text-[13.5px] [&_li]:text-[13.5px] text-[var(--color-text-secondary)]';
/** A note / landmark is second level: text size of the lane, headings included (a
 *  "## ✦ ⑦ TEST" landmark is a line inside the work, not a page heading). */
const NOTE_CLASS = 'text-[12.5px] [&_p]:text-[12.5px] [&_li]:text-[12.5px] [&_p]:text-[var(--color-text-secondary)] [&_li]:text-[var(--color-text-secondary)] '
  + '[&_h1]:text-[13px] [&_h2]:text-[13px] [&_h3]:text-[12.5px] [&_h1]:my-1 [&_h2]:my-1 [&_h3]:my-1 [&_h1]:text-[var(--color-text-secondary)] [&_h2]:text-[var(--color-text-secondary)]';

export function WorkGroupView({ group, compact, expanded, flat, onToggle, thoughtOpen, onThoughtToggle, renderNote, tail, defaultOpen }: WorkGroupViewProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const auto = useLinger(group.live);
  // In the FOLD_DELAY_MS after a block stops being live it keeps its live layout
  // (every item, the same folds), then folds in one move — nothing reshuffles under
  // the reader. A block open by default (a turn with no text) keeps it too.
  const lingering = !group.live && expanded === undefined && (auto || !!defaultOpen);
  const raw = useMemo(() => rawItems(group), [group]);
  const settled = useSettled(raw.map((it) => ({ key: it.key, done: itemDone(it) })), group.live || lingering);
  const items = foldItems(raw, settled);
  // A settled thought is one line; so is every thought of a block no longer live.
  const collapsed = (key: string) => !!compact || settled === null || settled.has(key);
  const running = toolSteps(group).some((s) => s.status === 'pending');
  const render = (it: Item) => {
    if ('thought' in it) {
      return (
        <ThoughtBlock
          key={it.key}
          unit={it.thought}
          compact={collapsed(it.key)}
          inGroup
          expanded={thoughtOpen(it.key)}
          onToggle={(open) => onThoughtToggle(it.key, open)}
        />
      );
    }
    if ('note' in it) {
      return (
        <div key={it.key} data-testid="group-note" className={NOTE_CLASS} {...ANCHOR_PROPS}>
          {renderNote(it.note.text, `${it.key}-b`)}
        </div>
      );
    }
    if ('fold' in it) return <FoldStrip key={it.key} rows={it.fold} />;
    return stripOf(it.row);
  };
  const showAllButton = (hidden: number) => hidden > 0 && (
    <button
      type="button"
      data-testid="show-all-steps"
      data-expands=""
      onClick={() => setShowAll(true)}
      className="px-1.5 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
    >
      {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: hidden })}
    </button>
  );

  if (flat) {
    // The stage row above is the fold; a live stage still says it is working. Live and
    // lingering show every item (settled calls fold, settled thoughts collapse — that
    // keeps it short); once done it shows the first PAGE.
    const shownFlat = showAll || group.live || lingering ? items : items.slice(0, PAGE);
    const hiddenFlat = items.length - shownFlat.length;
    return (
      <div data-testid="work-group" data-live={group.live ? 'true' : 'false'} data-flat="true" className="space-y-0.5">
        {group.live && showAllButton(hiddenFlat)}
        {shownFlat.map(render)}
        {!group.live && showAllButton(hiddenFlat)}
        {group.live && !running && (
          <div data-testid="group-row" role="status" className="flex items-center gap-2 px-1.5 text-[12.5px] text-[var(--color-text-muted)]">
            <Spinner />
            <span>{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
          </div>
        )}
      </div>
    );
  }

  if (group.live) {
    const shown = items; // live: everything, nothing cut off the top (folds keep it short)
    return (
      <div
        data-testid="work-group"
        data-live="true"
        className="my-1.5 border-l-2 border-[var(--color-text-muted)] bg-[var(--color-hover)]/40 rounded-r-lg px-2.5 py-1.5"
      >
        <div data-testid="group-row" role="status" className="flex items-center gap-2 min-w-0 text-[12.5px]">
          {!running && <Spinner size={14} />}
          <span className={`${TAG} text-[var(--color-text)]`}>{tf(t, 'chat.turn.group.working', 'Working')}</span>
          {items.length === 0 && <span className="text-[var(--color-text-secondary)]">{tf(t, 'chat.turn.thinking', 'Thinking')}</span>}
        </div>
        {items.length > 0 && (
          <div data-testid="group-body" className="mt-1 space-y-0.5">
            {shown.map(render)}
          </div>
        )}
      </div>
    );
  }

  if (items.length === 0) return null;
  const open = expanded ?? (auto || !!defaultOpen);
  // Lingering (above): the newest items, as while live; opened by hand: the first.
  // Lingering after live: every item, as while live. Open by default (a turn with no
  // text, never live here): its newest PAGE — where the turn stopped. Opened by hand:
  // the first PAGE.
  const shown = showAll || (lingering && auto) ? items : lingering ? items.slice(-PAGE) : items.slice(0, PAGE);
  const hidden = items.length - shown.length;
  const failure = group.members.find((m) => m.kind === 'process' && !!m.unrecoveredFailure);
  const c = workCounts(group);
  const counts = [
    // A block with no tool call (thinking only) says that, not "0 steps".
    c.steps === 0 && c.thoughts > 0 && tf(t, 'chat.turn.group.thoughts', c.thoughts === 1 ? '{{count}} thought' : '{{count}} thoughts', { count: c.thoughts }),
    c.steps > 0 && tf(t, 'chat.turn.group.steps', c.steps === 1 ? '{{count}} step' : '{{count}} steps', { count: c.steps }),
    c.files > 0 && tf(t, 'chat.turn.group.files', c.files === 1 ? '{{count}} file changed' : '{{count}} files changed', { count: c.files }),
    c.commands > 0 && tf(t, 'chat.turn.group.commands', c.commands === 1 ? '{{count}} command' : '{{count}} commands', { count: c.commands }),
  ].filter(Boolean).join(' · ');
  return (
    <div data-testid="work-group" data-live="false" className="my-0.5">
      <div data-testid="group-row" className="group/row flex items-center min-w-0 text-[12.5px] text-[var(--color-text-muted)]">
        <button
          type="button"
          data-testid="group-toggle"
          aria-expanded={open}
          onClick={() => onToggle(!open)}
          className="-ml-1 flex items-center gap-1.5 min-w-0 px-1 py-0.5 rounded-md text-left hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span
            aria-hidden="true"
            className={`material-symbols-outlined text-[15px] ${ROW_ICON_SLOT} text-[var(--color-text-dim)] transition-[transform,opacity] duration-150 ${open ? 'opacity-100' : 'opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100'}`}
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          >
            chevron_right
          </span>
          <span className={`${TAG} text-[var(--color-text-secondary)]`}>{tf(t, 'chat.turn.group.worked', 'Worked')}</span>
          {counts && <span data-testid="group-counts" className="truncate min-w-0 tabular-nums">· {counts}</span>}
        </button>
      </div>
      {open && (
        <div data-testid="group-body" data-live-layout={lingering ? 'true' : 'false'} className="ml-[7px] mt-0.5 mb-1 pl-2 border-l border-[var(--color-border)] space-y-0.5">
          {lingering && showAllButton(hidden)}
          {shown.map(render)}
          {!lingering && showAllButton(hidden)}
        </div>
      )}
      {/* A failure the turn never recovered from stays visible on the folded line. */}
      {!open && failure?.kind === 'process' && failure.unrecoveredFailure && (
        <div className="ml-[7px] pl-2 border-l border-red-500/40"><ToolStrip step={failure.unrecoveredFailure} /></div>
      )}
      {/* A turn with no result: its last note stays readable under the folded line
          (after the failure, if any). Opened, it is in its place in the list. */}
      {!open && tail && (tail.kind === 'note' || tail.kind === 'landmark') && (
        <div data-testid="turn-tail" className={TAIL_CLASS} {...ANCHOR_PROPS}>{renderNote(tail.text, 'tail-b')}</div>
      )}
    </div>
  );
}
