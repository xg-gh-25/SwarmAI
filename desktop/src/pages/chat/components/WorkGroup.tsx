/**
 * WorkGroup — a thought and the tool calls it led to, as one unit (chat display v7.11).
 *
 * Renders a `group` from `groupWork` (pages/chat/turnView.ts). Weight follows the
 * user's question, not the data volume (design-judgment Surface 6):
 *   - FINISHED: folded to one line — a `WORKED` tag (or `THOUGHT` for a group with
 *     no tool call), the group's title (its ":"-ended lead-in, else its first
 *     thought's first sentence) and the files it changed or touched as FileChips.
 *     The header is a button; the chips sit BESIDE it, never inside it (no nested
 *     interactive elements). Opened, it lists its thoughts and its strips in order.
 *     A group that is a single tool call, or a single thought, is that item itself —
 *     never a header that opens to the same line again.
 *   - LIVE (the last group of a streaming turn): an open `WORKING` card, no toggle.
 *     A running strip carries the spinner and its elapsed; with nothing running
 *     (the model is thinking between steps) the header holds the one spinner.
 *   - It folds FOLD_DELAY_MS after the next group starts (`useLinger`); a user's
 *     open/closed choice wins.
 *
 * ToolStrip — one tool call, one strip, no detail level: kind (Read / Search /
 * Write / Edit / Run / Test / Git / Pipeline / Query / Agent / Skill …) · target
 * (a FileChip when the call names a file) · a readable outcome of at most
 * INLINE_OUTCOME_CHARS · elapsed while running · status. An error is one line below.
 * A sub-agent's verdict / live progress sits on its Agent strip. The raw command is
 * never rendered (not as text, title or tooltip): it is audit material, not a row.
 * Status comes ONLY from the projection (`ToolStep.status`); colour only for
 * exceptions (failed red, waiting amber).
 *
 * @exports WorkGroupView, ToolStrip, stripKindWord, INLINE_OUTCOME_CHARS
 */
import { useMemo, useState } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { useLinger } from '../../../hooks/useLinger';
import type { ProcessUnit, StepRowItem, ThoughtUnit, ToolStep, WorkGroup } from '../turnView';
import { STRIP_KIND_FALLBACK, changedFiles, groupSteps, groupTitle, stepLabel, stripKind, summarizeProcess } from '../turnView';
import type { StepVerb, StripKind } from '../turnView';
import { tf } from '../tf';
import { FileChip } from './FileChip';
import { ThoughtBlock } from './ThoughtBlock';
import { ANCHOR_PROPS } from '../scrollFocus';

/** An outcome up to this long stays on its strip; a longer one is not repeated here. */
export const INLINE_OUTCOME_CHARS = 40;
/** A folded line names at most this many files. */
const MAX_LINE_FILES = 3;
/** A list (live: the newest; opened: the first) shows this many items before "show all". */
const PAGE = 12;

const COUNT_FALLBACK: Partial<Record<StepVerb, [string, string]>> = {
  read: ['read {{count}} file', 'read {{count}} files'],
  edit: ['changed {{count}} file', 'changed {{count}} files'],
  search: ['{{count}} search', '{{count}} searches'],
  run: ['ran {{count}} command', 'ran {{count}} commands'],
  fetch: ['fetched {{count}} page', 'fetched {{count}} pages'],
};

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

/** The strip's leading word; an unrecognised tool shows its own name, no invented verb. */
export function stripKindWord(t: TFunction, kind: StripKind, toolName?: string): string {
  if (kind === 'tool') return toolName || STRIP_KIND_FALLBACK.tool;
  return tf(t, `chat.turn.kind.${kind}`, STRIP_KIND_FALLBACK[kind]);
}

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

/** One tool call — or, with `merged`, adjacent finished reads of one file. */
export function ToolStrip({ step, merged }: { step: ToolStep; merged?: ToolStep[] }) {
  const { t } = useTranslation();
  const label = stepLabel(step.block);
  const kind = stripKind(step.block, label);
  const running = step.status === 'pending';
  const elapsed = usePendingToolElapsed(step.block.id, running);
  const isError = step.status === 'error';
  const all = merged ?? [step];
  const outcomes = [...new Set(all.map((s) => s.result?.outcome).filter((o): o is string => !!o))];
  const outcome = outcomes.join(', ');
  // A merged read keeps every range it returned; anything else only when it is short.
  const inline = kind !== 'agent' && !isError && !!outcome && (all.length > 1 || outcome.length <= INLINE_OUTCOME_CHARS) ? outcome : '';
  // A sub-agent: its live progress while it runs, its verdict once it is done.
  const p = step.progress;
  const subCount = Math.max(step.children?.length ?? 0, p?.toolUses ?? 0);
  const agentFacts: string[] = [];
  if (kind === 'agent') {
    if (subCount > 0) agentFacts.push(tf(t, 'chat.turn.steps', subCount === 1 ? '{{count}} step' : '{{count}} steps', { count: subCount }));
    if (running && p?.lastToolName) agentFacts.push(tf(t, 'chat.turn.nowUsing', 'now {{tool}}', { tool: p.lastToolName }));
  }
  // Its verdict: the live summary while the turn streams, the persisted result after.
  const verdictText = p?.summary || step.result?.outcome || '';
  const verdict = kind === 'agent' && !running && !isError ? verdictText.split('\n')[0].trim() : '';
  const agentFailed = p?.status === 'failed' || p?.status === 'stopped';
  return (
    <div data-testid="step-row" data-status={step.status} data-kind={kind} {...ANCHOR_PROPS}>
      <div className="flex items-center gap-2 px-1.5 py-[3px] rounded-md text-[12.5px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] min-w-0">
        <span data-testid="strip-kind" className={`shrink-0 w-[58px] truncate ${isError ? 'text-red-400' : 'text-[var(--color-text-secondary)]'}`}>
          {stripKindWord(t, kind, step.block.name)}
        </span>
        {label.path
          ? <FileChip path={label.path} name={label.object} diff={label.verb === 'edit'} />
          : !(kind === 'tool' && label.object === step.block.name) && (
            <span data-testid="strip-target" className="truncate min-w-0 text-[var(--color-text-secondary)]">{label.object}</span>
          )}
        {all.length > 1 && (
          <span data-testid="step-repeat" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">
            {tf(t, 'chat.turn.readTimes', '{{count}}×', { count: all.length })}
          </span>
        )}
        {inline && <span data-testid="step-outcome" className="truncate min-w-0 text-[12px] text-[var(--color-text-dim)]">{inline}</span>}
        {agentFacts.length > 0 && (
          <span data-testid="agent-facts" className="shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">· {agentFacts.join(' · ')}</span>
        )}
        {verdict && <span data-testid="agent-verdict" className="truncate min-w-0 text-[12px] text-[var(--color-text-dim)]" title={verdictText}>· {verdict}</span>}
        {agentFailed && <span className="shrink-0 text-[12px] text-red-400">· {tf(t, `chat.turn.subAgentStatus.${p?.status}`, p?.status ?? '')}</span>}
        <span className="ml-auto shrink-0 flex items-center gap-1.5 pl-2">
          {running && elapsed !== null && elapsed >= 1 && (
            <span data-testid="live-elapsed" className="text-[12px] text-[var(--color-text-muted)] tabular-nums">{formatSeconds(elapsed)}</span>
          )}
          <StatusIcon status={step.status} />
        </span>
      </div>
      {isError && outcome && (
        <div data-testid="step-error" className="ml-[66px] mr-2 mb-0.5 truncate text-[12px] text-red-400" title={outcome}>{outcome}</div>
      )}
    </div>
  );
}

/** Rows key by tool_use id: stable across the stream-end reconcile. */
const rowKey = (r: StepRowItem) => `tu-${(r.kind === 'reads' ? r.steps[0] : r.step).block.id}`;
const stripOf = (r: StepRowItem) => (r.kind === 'reads'
  ? <ToolStrip key={rowKey(r)} step={r.steps[0]} merged={r.steps} />
  : <ToolStrip key={rowKey(r)} step={r.step} />);

type Item = { key: string; thought: ThoughtUnit } | { key: string; row: StepRowItem };

/** A group's members as one ordered list of thoughts and strips. */
function itemsOf(g: WorkGroup): Item[] {
  const out: Item[] = [];
  for (const m of g.members) {
    if (m.kind === 'thought') out.push({ key: `th-${m.ordinal}`, thought: m });
    else for (const r of groupSteps(m.steps)) out.push({ key: rowKey(r), row: r });
  }
  return out;
}

const toolSteps = (g: WorkGroup): ToolStep[] =>
  g.members.flatMap((m) => (m.kind === 'process' ? m.steps.filter((s): s is ToolStep => s.kind === 'tool') : []));

/** The files a folded line names: what the group changed, else what it touched. */
function lineFiles(g: WorkGroup): { files: string[]; changed: boolean } {
  const steps = toolSteps(g);
  const changed = changedFiles(steps);
  if (changed.length) return { files: changed, changed: true };
  const touched: string[] = [];
  for (const s of steps) {
    const p = stepLabel(s.block).path;
    if (p && !touched.includes(p)) touched.push(p);
  }
  return { files: touched, changed: false };
}

export interface WorkGroupViewProps {
  group: WorkGroup;
  /** The turn is over: thoughts clamp to one line. */
  compact?: boolean;
  /** The user's open/closed choice for this group; undefined = automatic. */
  expanded?: boolean;
  /** Inside an opened pipeline stage: laid out open (title + content), so the stage
   *  row is the only fold between the user and its strips. */
  defaultOpen?: boolean;
  onToggle: (open: boolean) => void;
  /** The user's open choice for a thought (keyed `th-<ordinal>`). */
  thoughtOpen: (key: string) => boolean | undefined;
  onThoughtToggle: (key: string, open: boolean) => void;
}

export function WorkGroupView({ group, compact, expanded, defaultOpen, onToggle, thoughtOpen, onThoughtToggle }: WorkGroupViewProps) {
  const { t } = useTranslation();
  const [showAll, setShowAll] = useState(false);
  const auto = useLinger(group.live);
  const items = useMemo(() => itemsOf(group), [group]);
  const steps = useMemo(() => toolSteps(group), [group]);
  const title = groupTitle(group);
  const thoughtView = (th: ThoughtUnit, key: string) => (
    <ThoughtBlock
      key={key}
      unit={th}
      compact={compact}
      inGroup
      expanded={thoughtOpen(key)}
      onToggle={(open) => onThoughtToggle(key, open)}
    />
  );
  const render = (it: Item) => ('thought' in it ? thoughtView(it.thought, it.key) : stripOf(it.row));

  if (group.live) {
    const running = steps.some((s) => s.status === 'pending');
    const shown = showAll ? items : items.slice(-PAGE);
    const hidden = items.length - shown.length;
    return (
      <div
        data-testid="work-group"
        data-live="true"
        className="my-1.5 border-l-2 border-[var(--color-text-muted)] bg-[var(--color-hover)]/40 rounded-r-lg px-2.5 py-1.5"
      >
        <div data-testid="group-row" role="status" className="flex items-center gap-2 min-w-0 text-[12.5px]">
          {!running && <Spinner size={14} />}
          <span className="shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text)]">
            {tf(t, 'chat.turn.group.working', 'Working')}
          </span>
          <span data-testid="group-title" className="truncate min-w-0 text-[var(--color-text-secondary)]">
            {title || (items.length === 0 ? tf(t, 'chat.turn.thinking', 'Thinking') : '')}
          </span>
        </div>
        {items.length > 0 && (
          <div data-testid="group-body" className="mt-1 space-y-0.5">
            {hidden > 0 && (
              <button
                type="button"
                data-testid="show-all-steps"
                data-expands=""
                onClick={() => setShowAll(true)}
                className="px-1.5 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
              >
                {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: hidden })}
              </button>
            )}
            {shown.map(render)}
          </div>
        )}
      </div>
    );
  }

  // A group of one is the item itself — unless a lead-in titles it: then the title
  // is the header (it says something the item does not).
  if (items.length === 1 && !group.title) {
    return <div data-testid="work-group" data-live="false" data-single="true" className="my-0.5">{render(items[0])}</div>;
  }
  if (items.length === 0) return null;

  const open = expanded ?? (auto || !!defaultOpen);
  // In the FOLD_DELAY_MS after it finishes, the list keeps its live layout (the
  // newest items), then folds in one move — it does not reshuffle to its first items.
  const lingering = expanded === undefined && auto && !defaultOpen;
  const shown = showAll ? items : lingering ? items.slice(-PAGE) : items.slice(0, PAGE);
  const hidden = items.length - shown.length;
  const failure = group.members.find((m): m is ProcessUnit => m.kind === 'process' && !!m.unrecoveredFailure)?.unrecoveredFailure;
  const { files, changed } = lineFiles(group);
  // No title to name it by: say what it did instead ("read 3 files · ran 2 commands").
  const facts: string[] = [];
  if (!title) {
    const counts = group.members.flatMap((m) => (m.kind === 'process' ? summarizeProcess(m).counts : []));
    const merged = new Map<StepVerb, number>();
    for (const { verb, count } of counts) merged.set(verb, (merged.get(verb) ?? 0) + count);
    for (const [verb, count] of [...merged].sort((a, b) => b[1] - a[1]).slice(0, 2)) {
      const fb = COUNT_FALLBACK[verb];
      if (fb) facts.push(tf(t, `chat.turn.counts.${verb}`, count === 1 ? fb[0] : fb[1], { count }));
    }
    // Nothing to name it by (e.g. a run of plan / skill steps): its size, at least.
    if (!facts.length && !files.length) facts.push(tf(t, 'chat.turn.steps', '{{count}} steps', { count: steps.length }));
  }
  const tag = group.hasTools ? tf(t, 'chat.turn.group.worked', 'Worked') : tf(t, 'chat.turn.group.thought', 'Thought');
  return (
    <div data-testid="work-group" data-live="false" className="my-0.5">
      <div data-testid="group-row" className="group/row flex items-center gap-1.5 min-w-0 text-[12.5px] text-[var(--color-text-muted)]">
        <button
          type="button"
          data-testid="group-toggle"
          aria-expanded={open}
          onClick={() => onToggle(!open)}
          className="-ml-1 flex items-center gap-1.5 min-w-0 px-1 py-0.5 rounded-md text-left hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span
            aria-hidden="true"
            className={`material-symbols-outlined text-[15px] shrink-0 text-[var(--color-text-dim)] transition-[transform,opacity] duration-150 ${open ? 'opacity-100' : 'opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100'}`}
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          >
            chevron_right
          </span>
          <span className="shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">{tag}</span>
          {title && <span data-testid="group-title" className="truncate min-w-0 text-[var(--color-text-secondary)]" title={title}>{title}</span>}
          {facts.length > 0 && <span className="truncate min-w-0">{facts.join(' · ')}</span>}
        </button>
        {files.length > 0 && (
          <span data-testid="group-files" className="flex items-center gap-1 min-w-0 overflow-hidden shrink">
            {changed && <span className="shrink-0">· {tf(t, 'chat.turn.changed', 'changed')}</span>}
            {!changed && <span className="shrink-0">·</span>}
            {files.slice(0, MAX_LINE_FILES).map((p) => <FileChip key={p} path={p} diff={changed} />)}
            {files.length > MAX_LINE_FILES && (
              <span className="shrink-0">{tf(t, 'chat.turn.andMore', '+{{count}}', { count: files.length - MAX_LINE_FILES })}</span>
            )}
          </span>
        )}
      </div>
      {open && (
        <div data-testid="group-body" data-live-layout={lingering ? 'true' : 'false'} className="ml-[7px] mt-0.5 mb-1 pl-2 border-l border-[var(--color-border)] space-y-0.5">
          {!lingering && shown.map(render)}
          {hidden > 0 && (
            <button
              type="button"
              data-testid="show-all-steps"
              data-expands=""
              onClick={() => setShowAll(true)}
              className="px-1.5 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text-secondary)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
            >
              {tf(t, 'chat.turn.showAll', '{{count}} more · show all', { count: hidden })}
            </button>
          )}
          {lingering && shown.map(render)}
        </div>
      )}
      {/* A failure the turn never recovered from stays visible on the folded line. */}
      {!open && failure && <div className="ml-[7px] pl-2 border-l border-red-500/40"><ToolStrip step={failure} /></div>}
    </div>
  );
}
