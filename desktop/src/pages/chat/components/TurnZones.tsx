/**
 * TurnZones — renders a chat turn's zones (pages/chat/turnZones.ts) as the chat turn
 * spec v6 lays them out (Knowledge/Designs/2026-10-19-chat-turn-display-spec.md).
 *
 * Four kinds of group, and three textures inside them:
 * - `THINKING` / `THOUGHTS · N` (ThinkingGroup): the thoughts before anything was
 *   written or called, in full; open while the turn streams, folded after it, header kept.
 * - `WORKING` / `WORKED · N steps · N commands · time` (PhaseGroup): one per phase;
 *   open while live, folded FOLD_DELAY_MS after; the header toggles it.
 * - a pipeline stage (StageGroup): `✓ BUILD · stats · time`, the running one
 *   `● DELIVER · stats · next: REFLECT`, `round N`, `Stopped at X`; a gate is one
 *   verdict row (GateRow) with its earlier rounds' verdicts.
 * - `RESULT`: the label over the turn's answer text (a text zone with `result`); not
 *   foldable.
 * Inside a group, in stream order: thoughts (one row in the strip column, `✻ Thought`,
 * the first line, open for all of it), light strips (one call each; settled calls of one
 * kind merge as `Explored` / `Ran` / `Edited`, carrying the finished thoughts between
 * them, one tool with no thought as `Read ×N`) and Agents panels (one per batch of sub-agents that ran at the
 * same time). The text the agent wrote between calls stands where it was written and
 * carries the work after it under a ▸: while live only the latest text's work is open
 * (the fold moves on only while the user follows the bottom); opened later, all fold.
 *
 * Strips carry no failure mark and no red: a call that returned an error looks like the
 * others and shows its error when opened. Only what needs the user is a card. The one
 * animated signal is the streaming bee: a running strip, a running agent and a WORKING
 * header are static marks (amber strip, ⟳, the word WORKING).
 *
 * Fold choices are lifted (`choice(id)` / `choose(id, open)` from AssistantMessageView,
 * keyed by zone id) so they survive a remount, the stream-end reconcile and a tab switch.
 *
 * @exports TurnZoneList, ToolStripV6, AgentsPanel, PhaseBody, ZONE_GAP, RESULT_GAP
 */
import { memo, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { MouseEvent, ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { FOLD_DELAY_MS, useLinger } from '../../../hooks/useLinger';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { chatService } from '../../../services/chat';
import { ANCHOR_PROPS } from '../scrollFocus';
import { tf } from '../tf';
import { ASSISTANT_NAME } from '../assistantName';
import type { ThoughtUnit, ToolStep } from '../turnView';
import { STOP_TURN_EVENT, agentBatches, mergeColumn, mergeStrips, phaseSegments, stripLabel, stripView, zoneStats } from '../turnZones';
import type { PhaseMember, StageZone, StopReceiptView, StripItem, Zone } from '../turnZones';
import { ReadingContext } from '../readingContext';
import { attnProps, zoneAttention, PROCESS_PROSE } from '../attention';
import { StageResult } from './StageDocs';
import { agentStatus, useBackgroundAgents, useHasRunningAgent } from '../../../stores/backgroundAgentsStore';
import type { BackgroundAgent } from '../../../stores/backgroundAgentsStore';

/** Between two zones (spec §9.3): clearly more than anything inside a zone. */
export const ZONE_GAP = 'mt-5';
/** Above RESULT. */
export const RESULT_GAP = 'mt-7';
/** Inside a zone: header → body, and between a body's thought / strips / panel. */
const INNER_GAP = 'mt-2';
// ── The turn's column grid (docs/design/2026-10-24-turn-column-grid.md) ──────────
// A row's x is set by its KIND, never by how deep it is nested: every mark (▸ ✓ ● ◦)
// in the gutter (0, MARK_COL + gap-1.5 wide), every text row and every work row
// (strip, thought, Agents panel) at 22, a row's own opened detail at DETAIL_INSET
// from that row. These three constants are the only places the 22px offset is written
// (turnGrid.test.tsx guards it); nothing inside a group body adds left space again.
/** A group's body (thinking, phase, stage, stage result): moves it to the 22 column. */
const GROUP_BODY = 'ml-[22px]';
/** A first-level text zone (restatement, note, answer, RESULT, the stopped line). */
const TEXT_COL = 'pl-[22px]';
/** A row inside a group body (already at the column): its mark hangs in the gutter. */
const HANG = '-ml-[22px]';
/** Between two stage groups. */
const STAGE_GAP = 'mt-2';

const MIN_SECONDS = 5;
const fmt = (s: number) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`);
const sinceIso = (a?: string, b?: string): number | undefined => {
  const x = a ? Date.parse(a) : NaN;
  const y = b ? Date.parse(b) : NaN;
  return Number.isFinite(x) && Number.isFinite(y) && y >= x ? Math.round((y - x) / 1000) : undefined;
};

// ── View memory: how long a zone was live, as this view saw it ──────────────────
// Persisted blocks carry no timing, so a phase's / thought group's time is what this
// view saw (none from history). Kept per message scope for the app's lifetime, so a
// remount shows the same times. Bounded: the least recently used scopes are dropped
// (the live message is used on every render, so it is never the one dropped).
const spans = new Map<string, Map<string, { start: number; end?: number }>>();
/** The stopped line when the backend said why (core/turn_notice). */
const CAUSE_TEXT: Record<string, string> = {
  stalled: 'it stopped responding (for example while the computer slept)',
  oom: 'it ran out of memory',
  killed: 'something outside SwarmAI killed it (the computer was not short of memory)',
  timeout: 'it timed out',
  disconnected: 'the chat lost its connection to it',
  spawn_failed: 'it could not be started',
  rate_limit: 'the AI service was rate-limited',
  api_error: 'the AI service returned an error',
  zombie: 'it stopped responding',
  crash: 'it crashed',
};
/** causes that say nothing a person can use: the line goes without them */
const NO_CAUSE = new Set(['unknown']);
function stoppedNoticeText(t: Parameters<typeof tf>[0], n: { reason: string; cause?: string }): string {
  if (n.reason === 'superseded') {
    return tf(t, 'chat.turn.zone.stoppedSuperseded', 'Stopped · your newer message continued the conversation');
  }
  const cause = n.cause && !NO_CAUSE.has(n.cause) ? CAUSE_TEXT[n.cause] ?? n.cause : null;
  // the assistant is named once (pages/chat/assistantName), never "AI"
  const name = ASSISTANT_NAME;
  if (n.reason === 'gave_up') {
    // every restart attempt failed (any turn: a send, a continuation, a CLI-initiated turn)
    return cause
      ? tf(t, 'chat.turn.zone.stoppedGaveUpCause', 'Stopped · the {{name}} process could not be restarted ({{cause}}); send a message to continue', { cause, name })
      : tf(t, 'chat.turn.zone.stoppedGaveUp', 'Stopped · the {{name}} process could not be restarted; send a message to continue', { name });
  }
  return cause
    ? tf(t, 'chat.turn.zone.stoppedRestartedCause', 'Stopped · the {{name}} process was restarted because {{cause}}; the call it was running was lost', { cause, name })
    : tf(t, 'chat.turn.zone.stoppedRestarted', 'Stopped · the {{name}} process was restarted; the call it was running was lost', { name });
}

const SPAN_SCOPES = 200;
function useSpanSeconds(scope: string, id: string, live: boolean): number | undefined {
  let mem = spans.get(scope);
  if (mem) spans.delete(scope); // re-inserted below: the map is in last-used order
  else mem = new Map();
  spans.set(scope, mem);
  if (spans.size > SPAN_SCOPES) spans.delete(spans.keys().next().value as string);
  const now = Date.now();
  const seen = mem.get(id);
  if (live && !seen) mem.set(id, { start: now });
  else if (!live && seen && seen.end === undefined) seen.end = now;
  const s = mem.get(id);
  return s?.end !== undefined ? Math.round((s.end - s.start) / 1000) : undefined;
}
/** Forget every view's spans (tests). */
export function resetZoneMemory(): void { spans.clear(); calls.clear(); }

// How long each call ran, as this view saw it: from the first render that saw it
// running to the first render that saw it finished. History carries no call timing, so
// a call this app never saw running has none (its strip shows no time). Bounded: the
// oldest calls are dropped.
const calls = new Map<string, { start: number; end?: number }>();
const CALL_MEMORY = 5000;
function callSeconds(id: string, running: boolean): number | undefined {
  const now = Date.now();
  const seen = calls.get(id);
  if (running && !seen) {
    calls.set(id, { start: now });
    if (calls.size > CALL_MEMORY) calls.delete(calls.keys().next().value as string);
  } else if (!running && seen && seen.end === undefined) seen.end = now;
  const s = calls.get(id);
  return s?.end !== undefined ? Math.round((s.end - s.start) / 1000) : undefined;
}

/** Ids that have SETTLED: done, and held done for FOLD_DELAY_MS (or done when first
 *  seen). Only a settled call joins a merge and only a settled thought folds to one
 *  line, so nothing changes shape the moment a call finishes. Inactive → all settled.
 *
 *  `frozen` (the user scrolled up to read): nothing changes shape. What had settled
 *  stays settled, what had not stays open, no timer runs. On the way back each item
 *  still waiting restarts its FOLD_DELAY_MS, so nothing folds the moment they return. */
function useSettled(items: Array<{ id: string; done: boolean }>, active: boolean, frozen = false): (id: string) => boolean {
  const doneAt = useRef<Map<string, number> | null>(null);
  const frozenAt = useRef<number | null>(null);
  const [, setTick] = useState(0);
  let now = Date.now();
  if (doneAt.current === null) doneAt.current = new Map(items.filter((i) => i.done).map((i) => [i.id, -Infinity]));
  const map = doneAt.current;
  if (frozen) {
    if (frozenAt.current === null) frozenAt.current = now;
    now = frozenAt.current;
  } else {
    if (frozenAt.current !== null) {
      const at = frozenAt.current;
      const back = now;
      for (const [id, t] of map) if (t + FOLD_DELAY_MS > at) map.set(id, back);
      frozenAt.current = null;
    }
    for (const i of items) {
      if (i.done && !map.has(i.id)) map.set(i.id, now);
      // no longer done (a segment became the latest again): forget it, so it
      // settles FOLD_DELAY_MS after it is done again, not at once
      else if (!i.done) map.delete(i.id);
    }
  }
  const pending = items.filter((i) => i.done && map.has(i.id)).map((i) => map.get(i.id)!).filter((at) => at + FOLD_DELAY_MS > now);
  const nextAt = frozen || !pending.length ? null : Math.min(...pending) + FOLD_DELAY_MS;
  useEffect(() => {
    if (!active || nextAt === null) return undefined;
    const t = setTimeout(() => setTick((n) => n + 1), Math.max(0, nextAt - Date.now()) + 1);
    return () => clearTimeout(t);
  }, [active, nextAt]);
  if (!active) return () => true;
  return (id) => (map.get(id) ?? Infinity) + FOLD_DELAY_MS <= now;
}

/** The call ids of a phase's members, or of every phase in a stage's body. */
function callIds(items: Array<PhaseMember | Zone>): string[] {
  return items.flatMap((m) => {
    if (m.kind === 'process') return m.steps.map((s) => s.block.id);
    if (m.kind === 'phase') return callIds(m.members);
    return [];
  });
}

/**
 * True while one of these calls is a background Agent the daemon still runs, and
 * FOLD_DELAY_MS after it ends (never on the spot). The group holding it stays open at
 * every fold level (WORKED, a stage, a segment): its launch ack returns at once, so the
 * stream alone would call it finished and fold it out of sight while it works.
 */
function useHoldsRunningAgent(ids: string[], sessionId?: string): boolean {
  return useLinger(useHasRunningAgent(sessionId, ids));
}

// ── Group header ────────────────────────────────────────────────────────────────

const TAG = 'text-[11px] font-semibold uppercase tracking-[0.08em]';
/** The fixed-width mark column: a group's chevron, a stage's ✓ ●, a gate's ✓. */
const MARK_COL = 'w-4 shrink-0 text-center';

function Chevron({ open }: { open: boolean }) {
  return (
    <span aria-hidden="true" className={`material-symbols-outlined icon-sm inline-flex items-center justify-center h-4 leading-none ${MARK_COL} text-[var(--color-text-dim)] transition-transform duration-150`} style={{ transform: open ? 'rotate(90deg)' : 'none' }}>
      chevron_right
    </span>
  );
}

function GroupHeader({ testid, open, onToggle, mark, label, meta, labelClass }: {
  testid: string; open: boolean; onToggle: () => void; mark?: ReactNode; label: string; meta: string[]; labelClass?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-expanded={open}
      onClick={onToggle}
      className="-ml-1 flex items-center gap-1.5 min-w-0 max-w-full px-1 py-0.5 rounded-md text-left text-[12.5px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
    >
      {mark ?? <Chevron open={open} />}
      <span className={`${TAG} ${labelClass ?? 'text-[var(--color-text-secondary)]'}`}>{label}</span>
      {meta.length > 0 && <span data-testid="group-meta" className="truncate min-w-0 tabular-nums text-[var(--color-text-dim)]">· {meta.join(' · ')}</span>}
    </button>
  );
}

function statsMeta(t: ReturnType<typeof useTranslation>['t'], s: { steps: number; commands: number }, seconds?: number): string[] {
  return [
    s.steps > 0 && tf(t, 'chat.turn.zone.steps', s.steps === 1 ? '{{count}} step' : '{{count}} steps', { count: s.steps }),
    // every step a Bash command: the count is already said once
    s.commands > 0 && s.commands !== s.steps && tf(t, 'chat.turn.zone.commands', s.commands === 1 ? '{{count}} command' : '{{count}} commands', { count: s.commands }),
    seconds !== undefined && seconds >= MIN_SECONDS && fmt(seconds),
  ].filter((x): x is string => !!x);
}

// ── Thoughts ────────────────────────────────────────────────────────────────────

/** An opened strip row's body (a thought's text, a tool's detail): just inside the
 *  row's own inset, with a hairline guide, not under the preview column, so a long
 *  body keeps most of the width. */
const DETAIL_INSET = 'mt-0.5 mb-1 ml-2 pl-3 border-l border-[var(--color-border)]';

const THOUGHT_TEXT = 'text-[12.5px] italic leading-relaxed text-[var(--color-text-muted)] whitespace-pre-wrap break-words';

/** A thought inside a phase: one row in the strip column, `✻ Thought`, its first line
 *  and how long it ran; the row opens to all of it. Open, the row drops the preview:
 *  the full text below starts with that same line, shown once. The thought being
 *  written shows its first line too (the first line only grows, so the row never jumps):
 *  process prose does not take the reader's attention (attention tiers §6). The first
 *  THINKING passage of a turn is a ThinkingGroup, shown in full while written. */
function PhaseThought({ unit, scope, open, onToggle }: { unit: ThoughtUnit; scope: string; open: boolean; onToggle: (o: boolean) => void }) {
  const { t } = useTranslation();
  // The row's line: the first line of its newest paragraph (two thinking blocks in a row
  // are one passage, joined by a blank line), so while written the step arriving now is
  // on screen; the same rule once finished, so the row does not change when it ends.
  const first = (unit.text.trim().split(/\n\s*\n/).pop() ?? '').trim().split('\n')[0];
  const seconds = callSeconds(`${scope}:th-${unit.ordinal}`, unit.live);
  return (
    <div data-testid="thought" data-live={unit.live ? 'true' : 'false'} data-folded={open ? 'false' : 'true'} {...ANCHOR_PROPS}>
      <button
        type="button"
        data-testid="thought-toggle"
        aria-expanded={open}
        onClick={() => onToggle(!open)}
        className="w-full flex items-center gap-3 min-w-0 pl-2 pr-3 py-[3px] rounded-[6px] text-left hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        <span className="w-[10ch] shrink-0 truncate font-mono text-[12px] text-[var(--color-text-dim)]">✻ {tf(t, 'chat.turn.zone.thought', 'Thought')}</span>
        <span className="truncate min-w-0 text-[12.5px] italic text-[var(--color-text-muted)]">{open ? '' : first}</span>
        <span className="ml-auto w-[7ch] shrink-0 text-right font-mono text-[12px] tabular-nums text-[var(--color-text-muted)]">
          {seconds !== undefined && seconds >= MIN_SECONDS ? fmt(seconds) : ''}
        </span>
      </button>
      {open && <div data-testid="thought-text" className={`${THOUGHT_TEXT} ${DETAIL_INSET}`}>{unit.text}</div>}
    </div>
  );
}

export function ThinkingGroup({ zone, scope, open, onToggle }: {
  zone: Extract<Zone, { kind: 'thinking' }>; scope: string; open: boolean; onToggle: (o: boolean) => void;
}) {
  const { t } = useTranslation();
  const seconds = useSpanSeconds(scope, zone.id, zone.live);
  const label = zone.live ? tf(t, 'chat.turn.zone.thinking', 'Thinking') : tf(t, 'chat.turn.zone.thoughts', 'Thoughts');
  const meta = zone.live ? [] : [String(zone.thoughts.length), ...(seconds !== undefined && seconds >= MIN_SECONDS ? [fmt(seconds)] : [])];
  return (
    <div data-testid="zone-thinking" {...ANCHOR_PROPS}>
      <GroupHeader testid="thinking-toggle" open={open} onToggle={() => onToggle(!open)} label={label} meta={meta} />
      {open && (
        <div className={`${INNER_GAP} ${GROUP_BODY} space-y-2`}>
          {zone.thoughts.map((th) => <div key={`th-${th.ordinal}`} data-testid="thought" className={THOUGHT_TEXT}>{th.text}</div>)}
        </div>
      )}
    </div>
  );
}

// ── Strips ──────────────────────────────────────────────────────────────────────

/** A path shown as dim directory + bright file name; a long directory is cut from
 *  its start (the file name always stays whole). */
function PathObject({ dir, file }: { dir: string; file: string }) {
  return (
    <span className="min-w-0 flex items-baseline">
      <span dir="rtl" className="min-w-0 truncate text-[var(--color-text-dim)]"><bdi dir="ltr">{dir}</bdi></span>
      <span className="shrink-0 text-[var(--color-text-muted)]">{file}</span>
    </span>
  );
}

/** One strip: one call, or merged adjacent calls (`×N`). */
/** Two strip items show the same thing: same calls (by block), same status and
 *  result, same merged objects. deriveTurnView rebuilds every StripItem on each
 *  streamed token while the content blocks themselves keep their identity, so
 *  comparing by value lets a finished row skip the token's render. */
function sameStrip(a: { item: StripItem }, b: { item: StripItem }): boolean {
  const x = a.item;
  const y = b.item;
  if (x.key !== y.key || x.steps.length !== y.steps.length || (x.seq?.length ?? 0) !== (y.seq?.length ?? 0)
    || x.objects.join('\u0000') !== y.objects.join('\u0000')) return false;
  return x.steps.every((s, i) => {
    const t = y.steps[i];
    return s.block === t.block && s.status === t.status && s.result === t.result && s.progress === t.progress;
  });
}

/** A finished thought carried into a merged strip, listed when the strip is opened. Its
 *  own open state is local: the strip's detail is opened by hand and closes as a whole. */
function CarriedThought({ unit, scope }: { unit: ThoughtUnit; scope: string }) {
  const [open, setOpen] = useState(false);
  return <PhaseThought unit={unit} scope={scope} open={open} onToggle={setOpen} />;
}

export const ToolStripV6 = memo(function ToolStripV6({ item, scope = '' }: { item: StripItem; scope?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const step = item.steps[0];
  const v = stripView(step.block);
  const n = item.steps.length;
  const running = step.status === 'pending';
  const elapsed = usePendingToolElapsed(step.block.id, running);
  const stopped = step.status === 'unknown';
  const detail = n > 1 || !!step.result?.outcome;
  // finished: the time this view saw it run, summed over a merge (spec §3.1)
  const ran = item.steps.map((s) => callSeconds(s.block.id, s.status === 'pending'));
  const known = ran.filter((s): s is number => s !== undefined);
  const total = known.length ? known.reduce((a, b) => a + b, 0) : undefined;
  const time = running
    ? (elapsed !== null && elapsed >= MIN_SECONDS ? fmt(elapsed) : '')
    : stopped ? tf(t, 'chat.turn.zone.stoppedCall', 'stopped')
      : total !== undefined && total >= MIN_SECONDS ? fmt(total) : '';
  const label = stripLabel(item);
  const counts = label.verb ? [
    label.kind === 'run' ? tf(t, 'chat.turn.zone.commands', n === 1 ? '{{count}} command' : '{{count}} commands', { count: n })
      : label.kind === 'edit' ? tf(t, 'chat.turn.zone.edits', n === 1 ? '{{count}} edit' : '{{count}} edits', { count: n })
        : tf(t, 'chat.turn.zone.calls', n === 1 ? '{{count}} call' : '{{count}} calls', { count: n }),
    label.thoughts > 0 && tf(t, 'chat.turn.zone.thoughtCount', label.thoughts === 1 ? '{{count}} thought' : '{{count}} thoughts', { count: label.thoughts }),
  ].filter(Boolean).join(' · ') : '';
  // A merged row shows counts only: its calls are listed when it is opened, and in its title.
  const object = label.verb
    ? <span className="truncate min-w-0 text-[var(--color-text-secondary)]">{counts}</span>
    : n > 1
    ? null
    : v.dir !== undefined && v.file
      ? <PathObject dir={v.dir} file={v.file} />
      : <span className="truncate min-w-0 text-[var(--color-text-muted)]">{v.object}</span>;
  return (
    <div data-testid="tool-strip" data-status={step.status} data-tool={v.tool} {...ANCHOR_PROPS}>
      <button
        type="button"
        title={n > 1 ? item.steps.map((s) => stripView(s.block).full).join('\n') : v.full}
        aria-expanded={detail ? open : undefined}
        onClick={() => detail && setOpen((o) => !o)}
        className={`w-full flex items-center gap-3 min-w-0 pl-2 pr-3 py-[3px] rounded-[6px] font-mono text-[12px] text-left ${running
          ? 'bg-amber-500/10 hover:bg-amber-500/15'
          : 'bg-[var(--color-hover)]/60 hover:bg-[var(--color-hover)]'} focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50`}
      >
        <span data-testid="strip-tool" className={`w-[10ch] shrink-0 truncate ${running ? 'text-amber-500' : 'text-[var(--color-text-dim)]'}`}>
          {label.verb ? tf(t, `chat.turn.zone.verb.${label.kind}`, label.verb) : `${v.tool}${n > 1 ? ` ×${n}` : ''}`}
        </span>
        {object}
        <span data-testid="strip-time" className={`ml-auto w-[7ch] shrink-0 text-right tabular-nums ${stopped ? 'text-[var(--color-text-dim)]' : 'text-[var(--color-text-muted)]'}`}>{time}</span>
        {/* every strip keeps the slot, so the time column lines up with or without detail */}
        <span data-testid="strip-chevron" aria-hidden="true"
          className="material-symbols-outlined icon-xs w-[14px] shrink-0 -mr-1 inline-flex items-center justify-center leading-none text-[var(--color-text-dim)] transition-transform duration-150"
          style={open ? { transform: 'rotate(90deg)' } : undefined}>{detail ? 'chevron_right' : ''}</span>
      </button>
      {open && (
        <div data-testid="strip-detail" className={`${DETAIL_INSET} space-y-0.5 font-mono text-[11.5px] text-[var(--color-text-dim)]`}>
          {n > 1
            ? (item.seq ?? item.steps).map((s) => (s.kind === 'thought'
              ? <CarriedThought key={`th-${s.ordinal}`} unit={s} scope={scope} />
              : <ToolStripV6 key={`tu-${s.block.id}`} item={{ key: `tu-${s.block.id}`, steps: [s], objects: [] }} />))
            : <div className="whitespace-pre-wrap break-words">{step.result?.outcome}</div>}
        </div>
      )}
    </div>
  );
}, sameStrip);

/** A stop in one line (the call(s) it cut, the call(s) it kept from running, the
 *  background agents it ended; several of a kind are counted, not listed: each call
 *  already shows `stopped` on its own strip) and, for the detail, each item with what
 *  it left: an Agent's transcript, the files a Bash may have written, else "output not
 *  kept" (a stopped call keeps none). */
interface StopDetailRow { text: string; path?: boolean; label?: string }
function stopSummary(t: Parameters<typeof tf>[0], r: StopReceiptView): { line: string; full: StopDetailRow[] } {
  const cut = r.calls.filter((c) => c.ran !== false);
  const waiting = r.calls.filter((c) => c.ran === false);
  const parts: string[] = [];
  const full: StopDetailRow[] = [];
  const at = (ms?: number) => (ms !== undefined && ms >= MIN_SECONDS * 1000 ? fmt(Math.round(ms / 1000)) : '');
  if (cut.length === 1) {
    const d = at(cut[0].elapsedMs);
    parts.push(d ? tf(t, 'chat.turn.zone.stopCallAt', '{{tool}} stopped at {{time}}', { tool: cut[0].name, time: d })
      : tf(t, 'chat.turn.zone.stopCall', '{{tool}} stopped', { tool: cut[0].name }));
  } else if (cut.length > 1) parts.push(tf(t, 'chat.turn.zone.stopCalls', '{{count}} calls stopped', { count: cut.length }));
  if (waiting.length === 1) parts.push(tf(t, 'chat.turn.zone.stopNotRun', '{{tool}} not run', { tool: waiting[0].name }));
  else if (waiting.length > 1) parts.push(tf(t, 'chat.turn.zone.stopNotRunCount', '{{count}} calls not run', { count: waiting.length }));
  const agents = r.agents;
  if (agents.length === 1) parts.push(tf(t, 'chat.turn.zone.stopAgent', 'background agent {{name}} stopped',
    { name: agents[0].description || tf(t, 'chat.turn.zone.backgroundAgent', 'background agent') }));
  else if (agents.length > 1) parts.push(tf(t, 'chat.turn.zone.stopAgents', '{{count}} background agents stopped', { count: agents.length }));
  if (r.omitted) parts.push(tf(t, 'chat.turn.zone.stopOmitted', '+{{count}} more', { count: r.omitted }));
  // The detail rows (one item per line; a leftover path on its own line under it).
  const kept = (n?: number) => (n ? tf(t, 'chat.turn.zone.stopKeptCalls', '{{count}} calls kept, no conclusion', { count: n })
    : tf(t, 'chat.turn.zone.stopKeptNone', 'no conclusion'));
  for (const c of cut) {
    const head = `${c.name}${c.description ? ` · ${c.description}` : ''} · stopped${at(c.elapsedMs) ? ` at ${at(c.elapsedMs)}` : ''}`;
    if (c.transcript) {
      full.push({ text: `${head} · ${kept(c.transcript.toolCalls)}` });
      full.push({ text: c.transcript.path, path: true });
    } else {
      full.push({ text: `${head} · ${tf(t, 'chat.turn.zone.stopNoOutput', 'output not kept')}` });
      for (const w of c.writes ?? []) full.push({ text: w, path: true, label: tf(t, 'chat.turn.zone.stopMayHaveWritten', 'may have written') });
    }
  }
  for (const c of waiting) full.push({ text: `${c.name} · ${tf(t, 'chat.turn.zone.stopNotRunWhy', 'not run (it was waiting for your approval)')}` });
  for (const a of agents) {
    full.push({ text: `Agent · ${a.description || a.toolUseId} · background · ${a.status}${a.transcript ? ` · ${kept(a.transcript.toolCalls)}` : ''}` });
    if (a.transcript) full.push({ text: a.transcript.path, path: true });
  }
  return { line: parts.join(' · '), full };
}

/** The end of a turn the user stopped while something ran (backend turn_stop):
 *  "Stopped by you · …" in one line; a click opens what each stopped item left (its
 *  transcript, the files it may have written); then what Continue does. */
function StopReceiptBlock({ receipt }: { receipt: StopReceiptView }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { line, full } = stopSummary(t, receipt);
  return (
    <div data-testid="turn-stopped" data-receipt="" {...ANCHOR_PROPS}>
      <div className={HANG}>
      <button
        type="button"
        data-testid="stop-summary"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1.5 w-full min-w-0 text-left text-[12.5px] text-[var(--color-text-dim)] hover:text-[var(--color-text-muted)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 rounded-[4px]`}
      >
        <span data-testid="stop-mark" aria-hidden="true" className={MARK_COL}>{open ? '▾' : '▸'}</span>
        <span data-testid="stop-text" className="min-w-0 truncate">{tf(t, 'chat.turn.zone.stoppedByYouPlain', 'Stopped by you')}{line ? ` · ${line}` : ''}</span>
      </button>
      </div>
      {open && (
        <div data-testid="stop-detail" className={`${DETAIL_INSET} space-y-0.5 font-mono text-[11.5px] text-[var(--color-text-dim)] select-text`}>
          {full.map((r, i) => (
            <div key={i} data-testid={r.path ? 'stop-detail-path' : 'stop-detail-row'} className={r.path ? 'pl-4 break-all text-[var(--color-text-muted)]' : 'truncate'}>
              {r.label ? `${r.label}: ` : ''}{r.text}
            </div>
          ))}
        </div>
      )}
      <div data-testid="stop-continue-hint" className="mt-0.5 text-[12px] text-[var(--color-text-dim)]">
        {tf(t, 'chat.turn.zone.stopContinueHint', 'Continue picks up from here: the agent is told what was stopped and checks what it already did before going on.')}
      </div>
    </div>
  );
}

// ── Agents panel ────────────────────────────────────────────────────────────────

const AGENT_RESULT_MAX = 600;

/** An Agent started in the background (its call returned at once, the backend marks
 *  the result `background`): how it stands now, from the daemon's live view
 *  (stores/backgroundAgentsStore). null for any other Agent. */
type BgView = { status: string; durationMs?: number } | null;
const bgView = (s: ToolStep, agents: BackgroundAgent[], loaded: boolean): BgView =>
  agentStatus(agents, loaded, s.block.id, s.progress?.status, s.progress);
const inBackground = (bg: BgView) => bg?.status === 'running';

function AgentRow({ step, sessionId, readOnly, focused }: { step: ToolStep; sessionId?: string; readOnly?: boolean; focused?: boolean }) {
  const { t } = useTranslation();
  const { agents: bgAgents, loaded: bgLoaded } = useBackgroundAgents(sessionId);
  const bg = bgView(step, bgAgents, bgLoaded);
  const [open, setOpen] = useState(!!focused);
  const expanded = open || !!focused;
  useLayoutEffect(() => { if (focused) setOpen(true); }, [focused]);
  const [stopping, setStopping] = useState(false);
  const p = step.progress;
  const running = step.status === 'pending' && (p?.status === undefined || p.status === 'running');
  const background = !running && inBackground(bg);
  // the background agent ended (the daemon saw it end, or lost it)
  const bgEnd = bg && bg.status !== 'running' ? bg.status : null;
  // the daemon's live view of a background agent that runs (its progress, D1)
  const live = background && bg && 'startedAt' in bg ? (bg as BackgroundAgent) : undefined;
  // ticks once a second while it runs (a foreground agent: since it started here)
  const elapsed = usePendingToolElapsed(step.block.id, running || background);
  const desc = stripView(step.block).object || stripView(step.block).full;
  const tools = Math.max(step.children?.length ?? 0, p?.toolUses ?? 0, live?.toolUses ?? 0);
  const doneMs = bgEnd ? bg?.durationMs : p?.durationMs;
  const seconds = running ? elapsed ?? undefined
    : live?.startedAt !== undefined ? Math.max(0, Math.round((Date.now() - live.startedAt) / 1000))
      : doneMs !== undefined ? Math.round(doneMs / 1000) : undefined;
  const nowTool = running ? p?.lastToolName : live?.lastTool;
  const meta = [
    seconds !== undefined && seconds >= MIN_SECONDS && fmt(seconds),
    tools > 0 && tf(t, 'chat.turn.zone.tools', tools === 1 ? '{{count}} tool' : '{{count}} tools', { count: tools }),
  ].filter(Boolean).join(' · ');
  // Stopped: the SDK said so, or the turn ended with the agent neither finished nor
  // reported (the user stopped the turn; live progress is gone once streaming ends).
  const stoppedAgent = !background && !bgEnd && (p?.status === 'stopped' || (!running && step.status === 'unknown' && (!p || p.status === 'running')));
  const bgLabel = bgEnd === 'completed' ? tf(t, 'chat.turn.zone.doneInBackground', 'done in background')
    : bgEnd === 'failed' ? tf(t, 'chat.turn.zone.failedInBackground', 'failed in background')
      : bgEnd === 'stopped' ? tf(t, 'chat.turn.zone.stoppedCall', 'stopped')
        : bgEnd === 'lost' ? tf(t, 'chat.turn.zone.lostInBackground', 'lost: its process ended before it finished')
          : background ? tf(t, 'chat.turn.zone.inBackground', 'in background') : '';
  const mark = running ? '⟳' : background ? '↗' : bgEnd === 'completed' ? '✓' : bgEnd === 'failed' ? '✕' : bgEnd ? '■' : stoppedAgent ? '■' : '✓';
  const result = (p?.summary || step.result?.outcome || '').trim();
  const stop = async () => {
    if (!sessionId || !p?.taskId) return;
    setStopping(true);
    // A stop that did not take (refused, timed out, failed) re-enables the button: the
    // row stays running until the SDK reports, and the user can try again.
    let ok = false;
    try { ok = (await chatService.stopTask(sessionId, p.taskId)).status === 'stopped'; } catch { ok = false; }
    if (!ok) setStopping(false);
  };
  return (
    <div data-testid="agent-row" data-agent-id={step.block.id} data-status={running ? 'running' : bg?.status ?? p?.status ?? step.status} {...ANCHOR_PROPS}>
      <div className="flex items-start gap-2 min-w-0">
        <button
          type="button"
          data-testid="agent-toggle"
          aria-expanded={expanded}
          onClick={() => setOpen((o) => !o)}
          className="flex-1 flex items-start gap-2 min-w-0 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span aria-hidden="true" className={`${MARK_COL} text-[12px] text-purple-400`}>{mark}</span>
          <span className="min-w-0 flex-1 break-words text-[12.5px] text-[var(--color-text-secondary)]">{desc}</span>
          {meta && <span className="shrink-0 text-[12px] tabular-nums text-[var(--color-text-dim)]">{meta}</span>}
          {stoppedAgent && <span className="shrink-0 text-[12px] text-[var(--color-text-dim)]">{tf(t, 'chat.turn.zone.stoppedCall', 'stopped')}</span>}
          {bgLabel && <span data-testid="agent-background" className="shrink-0 text-[12px] text-[var(--color-text-dim)]">{bgLabel}</span>}
        </button>
        {running && !readOnly && p?.taskId && sessionId && (
          <button
            type="button"
            data-testid="agent-stop"
            disabled={stopping}
            onClick={stop}
            aria-label={tf(t, 'chat.turn.zone.stopAgentButton', 'Stop this agent')}
            title={tf(t, 'chat.turn.zone.stopAgentButton', 'Stop this agent')}
            className="shrink-0 px-1 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 disabled:opacity-40"
          >
            ✕
          </button>
        )}
      </div>
      {nowTool && (
        <div data-testid="agent-now" className="ml-6 font-mono text-[11.5px] text-purple-400">→ {nowTool}</div>
      )}
      {expanded && (
        <div data-testid="agent-detail" className="ml-6 mt-1 mb-1 space-y-1">
          <div className="text-[12px] text-[var(--color-text-muted)] whitespace-pre-wrap break-words">{stripView(step.block).full}</div>
          {(step.children?.length ?? 0) > 0 && (
            <div className="space-y-[2px]">
              {mergeStrips(step.children!, () => true).map((it) => <ToolStripV6 key={it.key} item={it} />)}
            </div>
          )}
          {result && (
            <div data-testid="agent-result" className="text-[12px] text-[var(--color-text-muted)] whitespace-pre-wrap break-words">
              {result.length > AGENT_RESULT_MAX ? `${result.slice(0, AGENT_RESULT_MAX - 1)}…` : result}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentsPanel({ agents, sessionId, readOnly, focusedAgentId }: {
  agents: ToolStep[];
  sessionId?: string;
  readOnly?: boolean;
  focusedAgentId?: string;
}) {
  const { t } = useTranslation();
  const focused = agents.some((agent) => agent.block.id === focusedAgentId);
  const [open, setOpen] = useState(true);
  const expanded = open || focused;
  useLayoutEffect(() => { if (focused) setOpen(true); }, [focused]);
  const isRunning = (s: ToolStep) => s.status === 'pending' && (s.progress?.status === undefined || s.progress.status === 'running');
  const { agents: bgAgents, loaded: bgLoaded } = useBackgroundAgents(sessionId);
  const bgOf = (s: ToolStep) => bgView(s, bgAgents, bgLoaded);
  const isBackground = (s: ToolStep) => !isRunning(s) && inBackground(bgOf(s));
  const isStopped = (s: ToolStep) => {
    if (isRunning(s) || isBackground(s)) return false;
    const end = bgOf(s)?.status;
    if (end) return end === 'stopped' || end === 'lost' || end === 'failed';
    return s.progress?.status === 'stopped' || (s.status === 'unknown' && (!s.progress || s.progress.status === 'running'));
  };
  const running = agents.filter(isRunning).length;
  const background = agents.filter(isBackground).length;
  const stopped = agents.filter(isStopped).length;
  const done = agents.length - running - background - stopped;
  const meta = [
    running > 0 && `⟳ ${tf(t, 'chat.turn.zone.running', '{{count}} running', { count: running })}`,
    background > 0 && `↗ ${tf(t, 'chat.turn.zone.backgroundCount', '{{count}} in background', { count: background })}`,
    done > 0 && `✓ ${tf(t, 'chat.turn.zone.done', '{{count}} done', { count: done })}`,
    stopped > 0 && `■ ${tf(t, 'chat.turn.zone.stoppedCount', '{{count}} stopped', { count: stopped })}`,
  ].filter((x): x is string => !!x);
  return (
    <section data-testid="agents-panel" className="rounded-[10px] border border-purple-400/25 bg-purple-500/[0.06] px-2.5 py-1.5" {...ANCHOR_PROPS}>
      <div className="flex items-center gap-2 min-w-0">
        <GroupHeader testid="agents-toggle" open={expanded} onToggle={() => setOpen((o) => !o)} label={tf(t, 'chat.turn.zone.agents', 'Agents')} meta={meta} labelClass="text-purple-400" />
        {running > 0 && !readOnly && (
          <button
            type="button"
            data-testid="agents-stop-all"
            onClick={() => window.dispatchEvent(new CustomEvent(STOP_TURN_EVENT, { detail: { sessionId } }))}
            className="ml-auto shrink-0 px-1.5 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
          >
            ✕ {tf(t, 'chat.turn.zone.stopAll', 'Stop all')}
          </button>
        )}
      </div>
      {expanded && (
        <div className="mt-1.5 space-y-1.5">
          {agents.map((a) => <AgentRow key={`ag-${a.block.id}`} step={a} sessionId={sessionId} readOnly={readOnly} focused={a.block.id === focusedAgentId} />)}
        </div>
      )}
    </section>
  );
}

// ── Phase ───────────────────────────────────────────────────────────────────────

export interface ZoneContext {
  scope: string;
  isStreaming: boolean;
  /** The turn ended FOLD_DELAY_MS ago or less (its groups keep their layout). */
  settling: boolean;
  sessionId?: string;
  readOnly?: boolean;
  choice: (id: string) => boolean | undefined;
  choose: (id: string, open: boolean) => void;
  /** Agent row requested from a later background-completion card. */
  focusedAgentId?: string;
  /** The agent's text (a note, an answer) and a needs block, rendered by the message. */
  renderText: (text: string, key: string) => ReactNode;
  renderNeeds: (zone: Extract<Zone, { kind: 'needs' | 'failure' }>) => ReactNode;
  /** A products zone's rows (design 2026-10-26-session-products.md §3.1), rendered by
   *  the message, which owns the deliverable look (TurnCards). */
  renderProducts?: (zone: Extract<Zone, { kind: 'products' }>) => ReactNode;
  /** A Swarm Out dispatch card (core/swarm_out_card), rendered by the message. */
  renderDispatch?: (zone: Extract<Zone, { kind: 'dispatch' }>) => ReactNode;
}

type BodyItem =
  | { key: string; thought: ThoughtUnit }
  | { key: string; strips: StripItem[] }
  | { key: string; agents: ToolStep[] };

/** Members as body items in stream order: thoughts, runs of strips, panels. `batchOf`
 *  is the whole phase's agent batching (a panel is not split by a note). Calls and
 *  thoughts between two panels form one column, merged by `mergeColumn` (a finished
 *  thought between two calls that merge goes into the merged strip). */
function bodyItems(members: PhaseMember[], settled: (id: string) => boolean, batchOf: Map<string, string>): BodyItem[] {
  const out: BodyItem[] = [];
  const panels = new Map<string, Extract<BodyItem, { agents: ToolStep[] }>>();
  let col: Array<ToolStep | ThoughtUnit> = [];
  const flush = () => {
    let run: StripItem[] = [];
    const flushRun = () => { if (run.length) out.push({ key: `strips-${run[0].steps[0].block.id}`, strips: run }); run = []; };
    for (const x of mergeColumn(col, settled)) {
      if ('steps' in x) { run.push(x); continue; }
      flushRun();
      out.push({ key: `th-${x.ordinal}`, thought: x });
    }
    flushRun();
    col = [];
  };
  for (const m of members) {
    if (m.kind === 'thought') { col.push(m); continue; }
    if (m.kind === 'text') continue; // the segments carry the text
    for (const s of m.steps) {
      const panelId = batchOf.get(s.block.id);
      if (panelId === undefined) { col.push(s); continue; }
      flush();
      const panel = panels.get(panelId);
      if (panel) panel.agents.push(s);
      else {
        const item = { key: `agents-${panelId}`, agents: [s] };
        panels.set(panelId, item);
        out.push(item);
      }
    }
  }
  flush();
  return out;
}

/** Text in a phase: the agent's words as written; ▸ opens the work under it. A click
 *  on a link or with text selected does not toggle. */
function PhaseNote({ text, renderKey, hasWork, open, onToggle, ctx }: {
  text: string; renderKey: string; hasWork: boolean; open: boolean; onToggle: (o: boolean) => void; ctx: ZoneContext;
}) {
  const toggle = (e: MouseEvent) => {
    if (!hasWork) return;
    const target = e.target as Element;
    if (target.closest('a, button:not([data-testid="segment-toggle"])')) return;
    if (window.getSelection()?.toString()) return;
    onToggle(!open);
  };
  return (
    <div data-testid="phase-text" {...PROCESS_PROSE} data-open={hasWork ? String(open) : undefined} onClick={toggle}
      className={`${HANG} flex items-start gap-1.5 text-[14px] ${hasWork ? 'cursor-pointer' : ''}`} {...ANCHOR_PROPS}>
      {hasWork
        ? (
          <button type="button" data-testid="segment-toggle" aria-expanded={open}
            className="mt-[3px] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50">
            <Chevron open={open} />
          </button>
        )
        : <span aria-hidden="true" className={MARK_COL} />}
      <div className="min-w-0 flex-1">{ctx.renderText(text, renderKey)}</div>
    </div>
  );
}

/**
 * A phase's body. The calls before the first text show as they are; each text the
 * agent wrote carries the work after it (up to the next text) under a ▸. While the
 * phase is live the latest text with work is open; an earlier one folds FOLD_DELAY_MS
 * after a later text started work (never on the spot), unless it holds a background
 * Agent still running, and only while the user follows the
 * bottom (ReadingContext). A folded phase opened later shows its texts folded. The
 * user's own toggle wins.
 */
export function PhaseBody({ members, live, ctx }: { members: PhaseMember[]; live: boolean; ctx: ZoneContext }) {
  const lingering = useLinger(live);
  const active = live || lingering;
  const reading = useContext(ReadingContext);
  const steps = members.flatMap((m) => (m.kind === 'process' ? m.steps : []));
  // while the user reads (scrolled up) nothing changes shape: no fold, no unfold
  const settled = useSettled(steps.map((s) => ({ id: s.block.id, done: s.status !== 'pending' })), active, reading);
  const batchOf = agentBatches(steps);
  const { lead, segments } = phaseSegments(members);
  // A segment folds FOLD_DELAY_MS after a LATER segment started work (a call, not just
  // its text), never on the spot, and only while the user follows the bottom: while
  // they read, nothing new folds. So the latest work stays open until newer work shows.
  // A segment holding a background Agent the daemon still runs stays open too: its
  // launch ack returns at once, so the stream alone would call it finished.
  const { agents: bgAgents } = useBackgroundAgents(ctx.sessionId);
  const bgRunning = new Set(bgAgents.filter((a) => a.status === 'running').map((a) => a.toolUseId));
  const segSteps = (sg: (typeof segments)[number]) => sg.members.flatMap((m) => (m.kind === 'process' ? m.steps : []));
  // Work is a call OR a thought: a segment whose only work is the thought being
  // written is the step working now. The newest segment never counts as done, even
  // before its work arrives — else it settled empty and its first thought appeared
  // already folded, i.e. the step working now was hidden (found by the stream-order
  // property test, 2026-10-22).
  const worked = segments.filter((sg) => sg.members.length > 0);
  const latest = worked.length ? `seg-tx-${worked[worked.length - 1].note.textOrdinal}` : null;
  const newest = segments.length ? `seg-tx-${segments[segments.length - 1].note.textOrdinal}` : null;
  const segSettled = useSettled(segments.map((sg) => {
    const id = `seg-tx-${sg.note.textOrdinal}`;
    const holdsRunningAgent = segSteps(sg).some((s) => bgRunning.has(s.block.id));
    return { id, done: id !== latest && id !== newest && !holdsRunningAgent };
  }), active, reading);

  const renderItems = (items: BodyItem[]) => {
    // Adjacent thoughts and strips share one column (strip spacing); a panel stands apart.
    const blocks: Array<{ key: string; column?: BodyItem[]; item?: BodyItem }> = [];
    for (const it of items) {
      const inColumn = 'thought' in it || 'strips' in it;
      const prev = blocks[blocks.length - 1];
      if (inColumn && prev?.column) prev.column.push(it);
      else if (inColumn) blocks.push({ key: `col-${it.key}`, column: [it] });
      else blocks.push({ key: it.key, item: it });
    }
    const columnRow = (it: BodyItem) => {
      if ('thought' in it) {
        const k = `th-${it.thought.ordinal}`;
        return <PhaseThought key={it.key} unit={it.thought} scope={ctx.scope} open={ctx.choice(k) ?? false} onToggle={(o) => ctx.choose(k, o)} />;
      }
      if ('strips' in it) return it.strips.map((s) => <ToolStripV6 key={s.key} item={s} scope={ctx.scope} />);
      return null;
    };
    return blocks.map((b) => {
      // one flat keyed list: a thought carried into a strip must not shift the rows after it
      if (b.column) return <div key={b.key} className="space-y-[2px]">{b.column.flatMap((it) => columnRow(it) ?? [])}</div>;
      const it = b.item!;
      if ('agents' in it) return <AgentsPanel key={b.key} agents={it.agents} sessionId={ctx.sessionId} readOnly={ctx.readOnly} focusedAgentId={ctx.focusedAgentId} />;
      return null;
    });
  };

  return (
    <div data-testid="phase-body" className="space-y-2">
      {lead.length > 0 && <div className="space-y-2">{renderItems(bodyItems(lead, settled, batchOf))}</div>}
      {segments.map((seg) => {
        const key = `seg-tx-${seg.note.textOrdinal}`;
        const hasWork = seg.members.length > 0;
        const open = hasWork && (ctx.choice(key) ?? !segSettled(key));
        return (
          <div key={key} data-testid="phase-segment" className="space-y-2">
            <PhaseNote text={seg.note.text} renderKey={`tx-${seg.note.textOrdinal}-b`} hasWork={hasWork} open={open}
              onToggle={(o) => ctx.choose(key, o)} ctx={ctx} />
            {open && <div className="space-y-2">{renderItems(bodyItems(seg.members, settled, batchOf))}</div>}
          </div>
        );
      })}
    </div>
  );
}

export function PhaseGroup({ zone, ctx }: { zone: Extract<Zone, { kind: 'phase' }>; ctx: ZoneContext }) {
  const { t } = useTranslation();
  const lingering = useLinger(zone.live);
  const seconds = useSpanSeconds(ctx.scope, zone.id, zone.live);
  const choice = ctx.choice(zone.id);
  // One clock for the turn's ending: the phase the turn ended in is `held` open for
  // the message's settle window and folds in the same frame the RESULT lands. Its own
  // linger only counts while the turn is still open (a phase that ended mid-stream).
  const turnOpen = ctx.isStreaming || ctx.settling;
  const holdsAgent = useHoldsRunningAgent(callIds(zone.members), ctx.sessionId);
  const open = choice ?? (zone.live || !!zone.held || (turnOpen && lingering) || holdsAgent);
  const stats = zoneStats(zone.members);
  const meta = stats.steps === 0 && stats.thoughts > 0
    ? [tf(t, 'chat.turn.zone.thoughtCount', stats.thoughts === 1 ? '{{count}} thought' : '{{count}} thoughts', { count: stats.thoughts })]
    : statsMeta(t, stats, zone.live ? undefined : seconds);
  return (
    <div data-testid="zone-phase" data-live={zone.live ? 'true' : 'false'} {...ANCHOR_PROPS}>
      <GroupHeader
        testid="group-toggle"
        open={open}
        onToggle={() => ctx.choose(zone.id, !open)}
        label={zone.live ? tf(t, 'chat.turn.zone.working', 'Working') : tf(t, 'chat.turn.zone.worked', 'Worked')}
        labelClass={zone.live ? 'text-[var(--color-text)]' : undefined}
        meta={meta}
      />
      {open && <div className={`${INNER_GAP} ${GROUP_BODY}`}><PhaseBody members={zone.members} live={zone.live} ctx={ctx} /></div>}
    </div>
  );
}

// ── Pipeline ────────────────────────────────────────────────────────────────────

const stageName = (s: string) => s.replace(/_/g, ' ').replace(/^gate(\d)$/i, 'gate $1').toUpperCase();

export function GateRow({ zone }: { zone: Extract<Zone, { kind: 'gate' }> }) {
  const { t } = useTranslation();
  const v = zone.verdict.trim().toUpperCase();
  const pass = /^(PASS|SUPPORTED|PROCEED|GO|DONE|SKIP(PED)?)$/.test(v);
  return (
    <div data-testid="zone-gate" data-stage={zone.stage} className="flex items-center gap-1.5 min-w-0 px-0 py-0.5 text-[12.5px]" {...ANCHOR_PROPS}>
      <span aria-hidden="true" className={`${MARK_COL} text-[12px] text-[var(--color-text-dim)]`}>{pass ? '✓' : '◦'}</span>
      <span className={`${TAG} text-[var(--color-text-secondary)]`}>{stageName(zone.stage)}</span>
      <span className={`${TAG} ${v === 'BLOCK' ? 'text-amber-500' : 'text-[var(--color-text-muted)]'}`}>{zone.verdict}</span>
      {zone.earlier.map((e, i) => (
        <span key={i} className="text-[12px] text-[var(--color-text-dim)]">· {tf(t, 'chat.turn.zone.round', 'round {{n}}', { n: i + 1 })}: {e}</span>
      ))}
    </div>
  );
}

export function StageGroup({ zone, ctx, mustOpen }: { zone: StageZone; ctx: ZoneContext; mustOpen: boolean }) {
  const { t } = useTranslation();
  const lingering = useLinger(zone.live);
  const choice = ctx.choice(zone.id);
  const turnOpen = ctx.isStreaming || ctx.settling; // same single ending clock as PhaseGroup
  const holdsAgent = useHoldsRunningAgent(callIds(zone.body), ctx.sessionId);
  const open = mustOpen || (choice ?? (zone.live || !!zone.held || (turnOpen && lingering) || zone.stoppedHere || holdsAgent));
  const seconds = zone.status !== 'running' ? sinceIso(zone.startedAt, zone.endedAt) : undefined;
  const meta = [
    ...statsMeta(t, zoneStats(zone.body), seconds),
    ...(zone.rounds > 1 ? [tf(t, 'chat.turn.zone.round', 'round {{n}}', { n: zone.rounds })] : []),
    ...(zone.live && zone.next ? [`${tf(t, 'chat.turn.zone.next', 'next:')} ${stageName(zone.next)}`] : []),
  ];
  const mark = <span aria-hidden="true" className={`${MARK_COL} text-[12px] ${zone.status === 'running' ? 'text-[var(--color-text)]' : 'text-[var(--color-text-dim)]'}`}>{zone.status === 'running' ? '●' : '✓'}</span>;
  const label = zone.stoppedHere ? tf(t, 'chat.turn.zone.stoppedAt', 'Stopped at {{stage}}', { stage: stageName(zone.stage) }) : stageName(zone.stage);
  return (
    <div data-testid="zone-stage" data-stage={zone.stage} data-status={zone.status} {...ANCHOR_PROPS}>
      <div className="flex items-center min-w-0">
        <GroupHeader testid="stage-toggle" open={open} onToggle={() => ctx.choose(zone.id, !open)} mark={mark} label={label} meta={meta}
          labelClass={zone.status === 'running' ? 'text-[var(--color-text)]' : undefined} />
      </div>
      {zone.status === 'done' && (zone.summary || zone.docs?.length) ? (
        // what the stage produced stays visible when its work is folded
        <div className={`${INNER_GAP} ${GROUP_BODY}`}>
          <StageResult summary={zone.summary} docs={zone.docs} renderText={ctx.renderText} />
        </div>
      ) : null}
      {open && zone.body.length > 0 && (
        <div className={`${INNER_GAP} ${GROUP_BODY} space-y-3`}>
          {zone.body.map((z) => <BodyZone key={z.id} zone={z} ctx={ctx} />)}
        </div>
      )}
    </div>
  );
}

/** A zone inside a stage: a phase renders its body directly (the stage is the fold);
 *  a note is the agent's text; a needs card stays a card. */
function BodyZone({ zone, ctx }: { zone: Zone; ctx: ZoneContext }) {
  if (zone.kind === 'phase') return <PhaseBody members={zone.members} live={zone.live} ctx={ctx} />;
  if (zone.kind === 'text') return <div data-testid="zone-note" data-attn={zoneAttention(zone)} {...PROCESS_PROSE} className="text-[14px]" {...ANCHOR_PROPS}>{ctx.renderText(zone.text, `${zone.id}-b`)}</div>;
  // a card and a stage-result row are positioned by kind, not by the stage body they
  // are in: back out to the first-level columns (card at 0, mark in the gutter)
  if (zone.kind === 'needs' || zone.kind === 'failure') return <div data-testid="card-slot" data-attn={zoneAttention(zone)} className={HANG}>{ctx.renderNeeds(zone)}</div>;
  if (zone.kind === 'stageResult') return <div data-attn={zoneAttention(zone)} className={HANG}><StageResultRow zone={zone} ctx={ctx} /></div>;
  if (zone.kind === 'products') return <div data-testid="zone-products" data-attn={zoneAttention(zone)} {...ANCHOR_PROPS}>{ctx.renderProducts?.(zone)}</div>;
  if (zone.kind === 'dispatch') return <div data-testid="zone-dispatch" data-attn={zoneAttention(zone)} {...ANCHOR_PROPS}>{ctx.renderDispatch?.(zone)}</div>;
  return null;
}

/** A direct run's completed stage (no stage groups there): its name, summary, documents. */
function StageResultRow({ zone, ctx }: { zone: Extract<Zone, { kind: 'stageResult' }>; ctx: ZoneContext }) {
  const b = zone.block;
  const docs = b.docs?.length ? b.docs : b.doc ? [b.doc] : [];
  return (
    <div data-testid="zone-stage-result" data-stage={b.stage} {...ANCHOR_PROPS}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span aria-hidden="true" className={`${MARK_COL} text-[12px] text-[var(--color-text-dim)]`}>✓</span>
        <span className={`${TAG} text-[var(--color-text-secondary)]`}>{stageName(b.stage)}</span>
      </div>
      <div className={`${INNER_GAP} ${GROUP_BODY}`}>
        <StageResult summary={b.summary} docs={docs} renderText={ctx.renderText} />
      </div>
    </div>
  );
}

// ── The list ────────────────────────────────────────────────────────────────────

/**
 * The turn's zones, the RESULT text included (the output card and the chips after it
 * are the message's): every zone keyed by its id (stable while streaming), with the two
 * spacing steps of spec §9.3.
 */
export function TurnZoneList({ zones, ctx, hasOpenQuestionIn }: {
  zones: Zone[]; ctx: ZoneContext; hasOpenQuestionIn: (z: StageZone) => boolean;
}) {
  const { t } = useTranslation();
  const turnOpen = ctx.isStreaming || ctx.settling;
  return (
    <>
      {zones.map((z, i) => {
        const prev = zones[i - 1];
        const stacked = prev && (prev.kind === 'stage' || prev.kind === 'gate') && (z.kind === 'stage' || z.kind === 'gate');
        const gap = !prev ? '' : z.kind === 'text' && z.result ? RESULT_GAP : stacked ? STAGE_GAP : ZONE_GAP;
        let el: ReactNode = null;
        switch (z.kind) {
          case 'thinking': {
            const c = ctx.choice(z.id);
            el = <ThinkingGroup zone={z} scope={ctx.scope} open={c ?? turnOpen} onToggle={(o) => ctx.choose(z.id, o)} />;
            break;
          }
          case 'phase': el = <PhaseGroup zone={z} ctx={ctx} />; break;
          case 'text':
            el = (
              <div
                data-testid={z.result ? 'turn-result' : z.role === 'restatement' ? 'turn-restatement' : z.role === 'note' ? 'turn-note' : 'turn-answer'}
                className={z.result ? `turn-result ${TEXT_COL}` : TEXT_COL}
                {...(z.result ? { role: 'region', 'aria-label': tf(t, 'chat.turn.result', 'Result') } : {})}
                {...ANCHOR_PROPS}
              >
                {ctx.renderText(z.text, `${z.id}-b`)}
              </div>
            );
            break;
          case 'needs': case 'failure': el = ctx.renderNeeds(z); break;
          case 'products': el = <div data-testid="zone-products" className={TEXT_COL} {...ANCHOR_PROPS}>{ctx.renderProducts?.(z)}</div>; break;
          case 'dispatch': el = <div data-testid="zone-dispatch" className={TEXT_COL} {...ANCHOR_PROPS}>{ctx.renderDispatch?.(z)}</div>; break;
          case 'pipelineStart':
            el = (
              <div data-testid="pipeline-start" className="flex items-center gap-2 text-[11.5px] text-[var(--color-text-dim)]" {...ANCHOR_PROPS}>
                <span className="w-6 border-t border-dashed border-[var(--color-border)]" />
                <span>{tf(t, 'chat.turn.zone.pipelineStarted', 'Pipeline started')}{z.profile ? ` · ${z.profile}` : ''}</span>
                <span className="flex-1 border-t border-dashed border-[var(--color-border)]" />
              </div>
            );
            break;
          case 'stage': el = <StageGroup zone={z} ctx={ctx} mustOpen={hasOpenQuestionIn(z)} />; break;
          case 'stageResult': el = <StageResultRow zone={z} ctx={ctx} />; break;
          case 'gate': el = <GateRow zone={z} />; break;
          case 'stopped':
            el = z.byUser && z.receipt && (z.receipt.calls.length || z.receipt.agents.length)
              ? <StopReceiptBlock receipt={z.receipt} />
              : (
              <div data-testid="turn-stopped" className="text-[12.5px] text-[var(--color-text-dim)]" {...ANCHOR_PROPS}>
                {z.byUser && (!z.unfinished || z.receipt)
                  ? tf(t, 'chat.turn.zone.stoppedByYouPlain', 'Stopped by you')
                  : z.byUser
                    ? tf(t, 'chat.turn.zone.stoppedByYou', 'Stopped by you · the last call did not finish')
                    : z.notice
                      ? stoppedNoticeText(t, z.notice)
                      : tf(t, 'chat.turn.zone.stopped', 'Stopped · the last call did not finish')}
              </div>
            );
            break;
          default: el = null;
        }
        // the stopped line and receipt sit in the text column, like every text row
        const cls = z.kind === 'stopped' ? `${gap} ${TEXT_COL}`.trim() : gap;
        return <div key={z.id} data-zone={z.kind} {...attnProps(z)} className={cls}>{el}</div>;
      })}
    </>
  );
}
