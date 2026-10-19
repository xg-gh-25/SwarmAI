/**
 * WorkGroup — one WORKING / WORKED block of a turn (chat display v8).
 *
 * Renders a `group` from `groupWork` (pages/chat/turnView.ts): EVERYTHING between two
 * first-level items of a turn (the restatement, the pipeline block, an interrupt, the
 * result). A turn has two levels (XG 2026-10-18):
 *   - first level: the restatement (plain text) · WORKING / WORKED · the pipeline
 *     block · interrupts · RESULT
 *   - second level, inside a block, in stream order: thoughts, notes the model wrote
 *     between tool calls, landmarks and tool calls
 * So:
 *   - A turn with no result: `tail` (its last note) shows under the folded line, like
 *     an unrecovered failure; opened, it is in its place in the list. `defaultOpen`
 *     opens the turn's last block when there is no text to show at all.
 *   - LIVE (the last block of a streaming turn): an open WORKING card, no toggle. A
 *     running tool call carries the spinner (and its elapsed, from 5s); with nothing
 *     running (the model is thinking) the header holds the one spinner.
 *   - FINISHED: folded to one WORKED line of counts — steps · files changed · commands.
 *     No file names, no title borrowed from a thought (it would repeat the first line of
 *     the body). Opened, it lists its items in order. It folds FOLD_DELAY_MS after it
 *     stops being live (`useLinger`); a user's open/closed choice wins.
 *   - `flat` (inside a pipeline stage): the stage line is the fold, so the items render
 *     directly, no WORKING / WORKED header of their own.
 *   - the live, lingering and opened bodies share ONE child layout (button slot,
 *     items, button slot): no item remounts when its block stops being live.
 *
 * ToolStrip — one tool call, one row, a step quieter than the thoughts around it
 * (Codex-style, XG 2026-10-19): a verb and the call's REAL object (`toolRow`) — Read
 * a.ts · Search "q" (grep / rg / find) · List src · Ran <program subcommand> (git
 * log -6, artifact_cli.py run-update) · Edited / Wrote b.ts · Agent <task> · an MCP
 * tool's own name. The whole command — minus only a leading cd / export setup and a
 * perl-alarm / timeout wrapper — is the row's second line while it runs (and
 * FOLD_DELAY_MS after) or once the row is opened. A file name is never cut (the chip
 * wraps). Adjacent reads of one file are one row (N×, with the ranges read). A
 * failure is one line below. The last line a command printed is never shown as a
 * "result". A duration shows only from 5s. A settled thought between this call and
 * the one before is merged into this row (`thought`), shown when it is opened.
 *
 * An Agent row shows its whole task description (wrapping) and no verdict. It is a
 * button: opened, it shows the task, its result and the sub-agent's own calls. It
 * says its elapsed while it runs and its duration once it finished.
 *
 * Density (`foldItems`): settled adjacent read / search / list calls are ONE
 * "Explored" row listing what they read and searched (a settled thought between
 * them goes inside it); identical consecutive other calls are one "×N" row. A
 * running, failed, possibly-writing or Agent call never folds. Nothing changes shape
 * the moment the next item arrives: a call joins a fold, a thought becomes its one
 * "Thought" line (or, between two calls, merges into the call after it), only
 * FOLD_DELAY_MS after it settled (`useSettled`, the same 2s
 * linger as every other fold). A live block cuts nothing off its top. A thought's
 * duration is how long this view saw it stream (none for one it never saw live).
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
import { changedFiles, groupSteps, stepLabel, toolCall, toolRow } from '../turnView';
import { tf } from '../tf';
import { FileChip } from './FileChip';
import { ThoughtBlock } from './ThoughtBlock';
import { ANCHOR_PROPS } from '../scrollFocus';

/** A list (live: the newest; opened: the first) shows this many items before "show all". */
const PAGE = 12;

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

/** A duration under this many seconds is not shown (it carries no information). */
const MIN_DURATION_S = 5;

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

/** One tool call — or, with `merged`, adjacent finished reads of one file. `thought`:
 *  a settled thought that sat between this call and the one before, merged into this
 *  line (XG spec: no line of its own; its time is this line's duration) and shown
 *  when the row is opened. */
export function ToolStrip({ step, merged, thought, thoughtSeconds }: {
  step: ToolStep; merged?: ToolStep[]; thought?: ReactNode; thoughtSeconds?: number;
}) {
  const { t } = useTranslation();
  const [agentOpen, setAgentOpen] = useState(false);
  const [allCalls, setAllCalls] = useState(false);
  const call = toolCall(step.block);
  const row = toolRow(step.block);
  const [cmdOpen, setCmdOpen] = useState(false);
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
  // The row says verb + the call's real object (Codex-style, chat display v8); the
  // full command is the second line while the call runs, or once the row is opened.
  const target = row.path
    ? <FileChip path={row.path} name={pathTail(row.path)} diff={verb === 'edit'} wrap />
    : !!row.object && (
      <span
        data-testid="strip-target"
        className={isAgent
          ? 'min-w-0 break-words text-[var(--color-text-muted)]'
          : 'truncate min-w-0 text-[12px] text-[var(--color-text-dim)]'}
        title={row.command || row.object}
      >
        {row.object}
      </span>
    );
  const hasCommand = !isAgent && !!row.command && row.command !== row.object;
  const hasDetail = hasCommand || !!thought;
  // The command line stays FOLD_DELAY_MS after the call finishes (nothing on screen
  // goes away the instant something changes), then only while the row is opened.
  const commandLingers = useLinger(running);
  const showCommand = hasCommand && (running || commandLingers || cmdOpen);
  const Row = isAgent ? 'button' : 'div';
  return (
    <div data-testid="step-row" data-status={step.status} data-tool={call.tool} className="group/strip" {...ANCHOR_PROPS}>
      <Row
        {...(isAgent ? {
          type: 'button' as const, 'data-testid': 'agent-toggle', 'aria-expanded': agentOpen,
          onClick: () => setAgentOpen((o) => !o),
        } : {})}
        className={`w-full flex ${isAgent ? 'items-start text-left cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : 'items-center'} gap-2 px-1.5 py-[3px] rounded-md text-[12px] text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] min-w-0`}
      >
        <span data-testid="strip-tool" title={call.tool} className={`shrink-0 min-w-[44px] max-w-[40%] truncate ${isError ? 'text-red-400' : 'text-[var(--color-text-muted)]'}`}>
          {row.verb}
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
          {running && elapsed !== null && elapsed >= MIN_DURATION_S && (
            <span data-testid="live-elapsed" className="text-[12px] text-[var(--color-text-muted)] tabular-nums">{formatSeconds(elapsed)}</span>
          )}
          {typeof durationMs === 'number' && durationMs >= MIN_DURATION_S * 1000 && (
            <span data-testid="agent-duration" className="text-[12px] text-[var(--color-text-dim)] tabular-nums">{formatSeconds(Math.round(durationMs / 1000))}</span>
          )}
          {!running && typeof thoughtSeconds === 'number' && thoughtSeconds >= MIN_DURATION_S && (
            <span data-testid="step-duration" className="text-[12px] text-[var(--color-text-dim)] tabular-nums">{formatSeconds(thoughtSeconds)}</span>
          )}
          {hasDetail && !running && (
            <button
              type="button"
              data-testid="row-toggle"
              aria-expanded={cmdOpen}
              aria-label={tf(t, 'chat.turn.showCommand', 'Show the command')}
              onClick={() => setCmdOpen((o) => !o)}
              className="material-symbols-outlined text-[14px] leading-none text-[var(--color-text-dim)] opacity-0 group-hover/strip:opacity-100 focus-visible:opacity-100 rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
              style={{ transform: cmdOpen ? 'rotate(180deg)' : 'none' }}
            >
              expand_more
            </button>
          )}
          <StatusIcon status={step.status} />
        </span>
      </Row>
      {cmdOpen && thought && <div data-testid="step-thought" className="ml-[60px] mr-2 mb-0.5">{thought}</div>}
      {showCommand && (
        <div data-testid="step-command" className="ml-[60px] mr-2 mb-0.5 font-mono text-[11px] text-[var(--color-text-dim)] whitespace-pre-wrap break-all">{row.command}</div>
      )}
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
                {foldRuns(groupSteps(shown)).map((it) => ('fold' in it ? <FoldStrip key={it.key} rows={it.fold} explore={it.explore} /> : stripOf(it.row)))}
                {!running && more}
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

const firstStep = (r: StepRowItem): ToolStep => (r.kind === 'reads' ? r.steps[0] : r.step);
const stepsOf = (r: StepRowItem): ToolStep[] => (r.kind === 'reads' ? r.steps : [r.step]);
/** Same verb and object: identical consecutive calls fold into one "×N" line. */
const rowSig = (r: StepRowItem): string => { const v = toolRow(firstStep(r).block); return `${v.verb}\u0000${v.object}`; };
/** The objects shown on an Explored line, per verb. */
const EXPLORED_OBJECTS = 4;

/**
 * A folded run of settled calls (chat display v8, Codex-style):
 * - `explore`: adjacent read / search / list calls — "Explored  Read a.ts, b.ts ·
 *   Search "q"" (objects deduplicated); it opens to one row per call, with any thought
 *   that sat between them, in order;
 * - otherwise identical consecutive calls — "Ran make test ×3".
 */
function FoldStrip({ rows, explore, members, renderMember }: {
  rows: StepRowItem[];
  explore: boolean;
  members?: Item[];
  renderMember?: (it: Item) => ReactNode;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  let label: ReactNode;
  if (explore) {
    const byVerb = new Map<string, string[]>();
    for (const r of rows) {
      const v = toolRow(firstStep(r).block);
      const objs = byVerb.get(v.verb) ?? [];
      if (v.object && !objs.includes(v.object)) objs.push(v.object);
      byVerb.set(v.verb, objs);
    }
    label = (
      <>
        <span className="shrink-0 font-semibold text-[var(--color-text-muted)]">{tf(t, 'chat.turn.explored', 'Explored')}</span>
        <span className="truncate min-w-0">
          {[...byVerb].map(([verb, objs], i) => (
            <span key={verb}>
              {i > 0 && <span className="text-[var(--color-text-dim)]"> · </span>}
              <span className="text-[var(--color-text-muted)]">{verb}</span>{' '}
              <span className="text-[var(--color-text-secondary)]">
                {objs.slice(0, EXPLORED_OBJECTS).join(', ')}{objs.length > EXPLORED_OBJECTS ? ` +${objs.length - EXPLORED_OBJECTS}` : ''}
              </span>
            </span>
          ))}
        </span>
      </>
    );
  } else {
    const v = toolRow(firstStep(rows[0]).block);
    const n = rows.reduce((k, r) => k + stepsOf(r).length, 0);
    label = (
      <>
        <span className="shrink-0 text-[var(--color-text-muted)]">{v.verb}</span>
        <span className="truncate min-w-0 text-[var(--color-text-secondary)]">{v.object}</span>
        <span className="shrink-0 tabular-nums">{tf(t, 'chat.turn.times', '×{{count}}', { count: n })}</span>
      </>
    );
  }
  const body = members && renderMember ? members.map(renderMember) : rows.map(stripOf);
  return (
    <div data-testid={explore ? 'step-explored' : 'step-fold'} {...ANCHOR_PROPS}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 min-w-0 px-1.5 py-[3px] rounded-md text-left text-[12px] text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        <span aria-hidden="true" className="material-symbols-outlined text-[14px] shrink-0 transition-transform duration-150" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>chevron_right</span>
        {label}
        <span className="ml-auto shrink-0"><StatusIcon status="done" /></span>
      </button>
      {open && <div className="ml-[7px] pl-1 border-l border-[var(--color-border)] space-y-0.5">{body}</div>}
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
  // `before`: a settled thought merged into this call's line (foldItems). Never named
  // `thought` — `'thought' in it` is what marks a thought item.
  | { key: string; row: StepRowItem; before?: Extract<Item, { thought: ThoughtUnit }> }
  | { key: string; fold: StepRowItem[]; explore: boolean; members?: Item[] };

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

/** Settled rows fold (chat display v8): adjacent read / search / list rows become ONE
 *  "Explored" item — a settled thought between two of them goes inside it — and
 *  identical consecutive other calls one "×N" item. A fold keys by its first row, so it
 *  stays the same element while later calls join it. Only rows the classifier calls
 *  read-only can be exploration (`toolRow().explore`): a call that may write never is. */
function foldItems(items: Item[], settled: Set<string> | null = null, keep: (key: string) => boolean = () => false): Item[] {
  const out: Item[] = [];
  let run: { rows: StepRowItem[]; members: Item[] } | null = null;
  let held: Item[] = []; // settled thoughts after the run's last row, not yet inside it
  const isSettled = (key: string) => settled === null || settled.has(key);
  const flushRun = () => {
    if (run) {
      const single = run.rows.length === 1 && run.members.length === 1;
      if (single) out.push(run.members[0]);
      else out.push({ key: `fold-${rowKey(run.rows[0])}`, fold: run.rows, explore: true, members: run.members });
    }
    run = null;
    out.push(...held);
    held = [];
  };
  for (const it of items) {
    const row = 'row' in it && foldable(it.row, settled) ? it.row : null;
    if (row && toolRow(firstStep(row).block).explore) {
      if (run) { run.members.push(...held, it); run.rows.push(row); held = []; }
      else run = { rows: [row], members: [it] };
      continue;
    }
    if (run && 'thought' in it && isSettled(it.key)) { held.push(it); continue; }
    flushRun();
    const prev = out[out.length - 1];
    if (row && prev) {
      const prevRows = 'fold' in prev && !prev.explore ? prev.fold : 'row' in prev && foldable(prev.row, settled) ? [prev.row] : null;
      if (prevRows && rowSig(prevRows[0]) === rowSig(row)) {
        out[out.length - 1] = { key: 'fold' in prev ? prev.key : `fold-${rowKey(prevRows[0])}`, fold: [...prevRows, row], explore: false };
        continue;
      }
    }
    out.push(it);
  }
  flushRun();
  // A settled thought between two calls is not a line of its own: it merges into the
  // call after it (shown when that row is opened; its time is the row's duration).
  // Only into a call that has settled too — a running row cannot be opened yet, and a
  // call that just finished must not take the thought on the spot. A thought the user
  // opened (`keep`) stays where it is.
  const isCall = (it: Item | undefined) => !!it && ('row' in it || 'fold' in it);
  for (let i = 1; i < out.length - 1; i += 1) {
    const it = out[i];
    const next = out[i + 1];
    if (!('thought' in it) || !isSettled(it.key) || !isCall(out[i - 1])) continue;
    if ('row' in next && !next.before && isSettled(next.key) && !keep(it.key)) {
      out[i + 1] = { ...next, before: it };
      out.splice(i, 1);
      i -= 1;
    } else if ('fold' in next && next.explore && next.members && !keep(it.key)) {
      out[i + 1] = { ...next, members: [it, ...next.members] };
      out.splice(i, 1);
      i -= 1;
    }
  }
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

export function WorkGroupView({ group, expanded, flat, onToggle, thoughtOpen, onThoughtToggle, renderNote, tail, defaultOpen }: WorkGroupViewProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const auto = useLinger(group.live);
  // In the FOLD_DELAY_MS after a block stops being live it keeps its live layout
  // (every item, the same folds), then folds in one move — nothing reshuffles under
  // the reader. A block open by default (a turn with no text) keeps it too.
  const lingering = !group.live && expanded === undefined && (auto || !!defaultOpen);
  const raw = useMemo(() => rawItems(group), [group]);
  const settled = useSettled(raw.map((it) => ({ key: it.key, done: itemDone(it) })), group.live || lingering);
  // A thought the user opened as a line stays a line (merging would hide what they
  // opened); once merged into a call row it stays merged — opening it there must not
  // move it back out.
  const merged = useRef(new Set<string>());
  const items = foldItems(raw, settled, (k) => thoughtOpen(k) === true && !merged.current.has(k));
  for (const it of items) if ('row' in it && it.before) merged.current.add(it.before.key);
  // A settled thought is one line; so is every thought of a block no longer live and
  // no longer lingering (a turn that just ended keeps its thoughts whole FOLD_DELAY_MS).
  const collapsed = (key: string) => settled === null || settled.has(key);
  const running = toolSteps(group).some((s) => s.status === 'pending');
  // How long each thought streamed, as this view saw it: from the first render it was
  // live to the first render it was not. A thought that was never live here (history,
  // a remount after it finished) has no duration — persisted blocks carry no timing.
  const thoughtTimes = useRef(new Map<string, { start: number; end?: number }>());
  const now = Date.now();
  for (const it of raw) {
    if (!('thought' in it)) continue;
    const seen = thoughtTimes.current.get(it.key);
    if (it.thought.live) { if (!seen) thoughtTimes.current.set(it.key, { start: now }); }
    else if (seen && seen.end === undefined) seen.end = now;
  }
  // How long the block itself was live, as this view saw it (none from history —
  // persisted blocks carry no timing): the WORKED line says it from 5s.
  const liveSpan = useRef<{ start: number; end?: number } | null>(null);
  if (group.live && !liveSpan.current) liveSpan.current = { start: now };
  else if (!group.live && liveSpan.current && liveSpan.current.end === undefined) liveSpan.current.end = now;
  const workedSeconds = liveSpan.current?.end === undefined ? undefined : Math.round((liveSpan.current.end - liveSpan.current.start) / 1000);
  const secondsOf = (key: string): number | undefined => {
    const seen = thoughtTimes.current.get(key);
    return seen?.end === undefined ? undefined : Math.round((seen.end - seen.start) / 1000);
  };
  const render = (it: Item): ReactNode => {
    if ('thought' in it) {
      return (
        <ThoughtBlock
          key={it.key}
          unit={it.thought}
          compact={collapsed(it.key)}
          inGroup
          expanded={thoughtOpen(it.key)}
          onToggle={(open) => onThoughtToggle(it.key, open)}
          seconds={secondsOf(it.key)}
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
    if ('fold' in it) return <FoldStrip key={it.key} rows={it.fold} explore={it.explore} members={it.members} renderMember={render} />;
    if (it.before) {
      const r = it.row;
      const props = { thought: render(it.before), thoughtSeconds: secondsOf(it.before.key) };
      return r.kind === 'reads'
        ? <ToolStrip key={rowKey(r)} step={r.steps[0]} merged={r.steps} {...props} />
        : <ToolStrip key={rowKey(r)} step={r.step} {...props} />;
    }
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
        {/* One child layout in every branch (button slot, items, button slot): the
            items keep their slot, so a block that stops being live never remounts them. */}
        {group.live ? showAllButton(hiddenFlat) : null}
        {shownFlat.map(render)}
        {!group.live ? showAllButton(hiddenFlat) : null}
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
            {null}
            {shown.map(render)}
            {null}
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
    workedSeconds !== undefined && workedSeconds >= MIN_DURATION_S && formatSeconds(workedSeconds),
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
          {/* The same three slots as the live body: the items do not remount when the
              block stops being live. */}
          {lingering ? showAllButton(hidden) : null}
          {shown.map(render)}
          {!lingering ? showAllButton(hidden) : null}
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
