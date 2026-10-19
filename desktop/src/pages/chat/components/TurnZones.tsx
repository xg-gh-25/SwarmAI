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
 * Inside a group, in stream order: thoughts (one line, the first line of the text, open
 * for all of it), light strips (one call each; `Read ×N` / `Bash ×N` merges) and Agents
 * panels (one per batch of sub-agents that ran at the same time). The notes the agent
 * wrote between calls are never inside a group: they are zones of their own.
 *
 * Strips carry no failure mark and no red: a call that returned an error looks like the
 * others and shows its error when opened. Only what needs the user is a card. The one
 * animated signal is the streaming bee: a running strip, a running agent and a WORKING
 * header are static marks (amber strip, ⟳, the word WORKING).
 *
 * Fold choices are lifted (`choice(id)` / `choose(id, open)` from AssistantMessageView,
 * keyed by zone id) so they survive a remount, the stream-end reconcile and a tab switch.
 *
 * @exports TurnZoneList, ToolStripV6, AgentsPanel, ZONE_GAP, RESULT_GAP
 */
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { FOLD_DELAY_MS, useLinger } from '../../../hooks/useLinger';
import { usePendingToolElapsed } from '../../../hooks/usePendingToolElapsed';
import { chatService } from '../../../services/chat';
import { ANCHOR_PROPS } from '../scrollFocus';
import { tf } from '../tf';
import type { ThoughtUnit, ToolStep } from '../turnView';
import { STOP_TURN_EVENT, agentBatches, mergeStrips, stripView, zoneStats } from '../turnZones';
import type { PhaseMember, StageZone, StripItem, Zone } from '../turnZones';

/** Between two zones (spec §9.3): clearly more than anything inside a zone. */
export const ZONE_GAP = 'mt-5';
/** Above RESULT. */
export const RESULT_GAP = 'mt-7';
/** Inside a zone: header → body, and between a body's thought / strips / panel. */
const INNER_GAP = 'mt-2';
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
export function resetZoneMemory(): void { spans.clear(); }

/** Ids that have SETTLED: done, and held done for FOLD_DELAY_MS (or done when first
 *  seen). Only a settled call joins a merge and only a settled thought folds to one
 *  line, so nothing changes shape the moment a call finishes. Inactive → all settled. */
function useSettled(items: Array<{ id: string; done: boolean }>, active: boolean): (id: string) => boolean {
  const doneAt = useRef<Map<string, number> | null>(null);
  const [, setTick] = useState(0);
  const now = Date.now();
  if (doneAt.current === null) doneAt.current = new Map(items.filter((i) => i.done).map((i) => [i.id, -Infinity]));
  else for (const i of items) if (i.done && !doneAt.current.has(i.id)) doneAt.current.set(i.id, now);
  const map = doneAt.current;
  const pending = items.filter((i) => i.done).map((i) => map.get(i.id)!).filter((at) => at + FOLD_DELAY_MS > now);
  const nextAt = pending.length ? Math.min(...pending) + FOLD_DELAY_MS : null;
  useEffect(() => {
    if (!active || nextAt === null) return undefined;
    const t = setTimeout(() => setTick((n) => n + 1), Math.max(0, nextAt - Date.now()) + 1);
    return () => clearTimeout(t);
  }, [active, nextAt]);
  if (!active) return () => true;
  return (id) => (map.get(id) ?? Infinity) + FOLD_DELAY_MS <= now;
}

// ── Group header ────────────────────────────────────────────────────────────────

const TAG = 'text-[11px] font-semibold uppercase tracking-[0.08em]';
/** The fixed-width mark column: a group's chevron, a stage's ✓ ●, a gate's ✓. */
const MARK_COL = 'w-4 shrink-0 text-center';

function Chevron({ open }: { open: boolean }) {
  return (
    <span aria-hidden="true" className={`material-symbols-outlined inline-flex items-center justify-center h-4 text-[15px] leading-none ${MARK_COL} text-[var(--color-text-dim)] transition-transform duration-150`} style={{ transform: open ? 'rotate(90deg)' : 'none' }}>
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
    s.commands > 0 && tf(t, 'chat.turn.zone.commands', s.commands === 1 ? '{{count}} command' : '{{count}} commands', { count: s.commands }),
    seconds !== undefined && seconds >= MIN_SECONDS && fmt(seconds),
  ].filter((x): x is string => !!x);
}

// ── Thoughts ────────────────────────────────────────────────────────────────────

const THOUGHT_TEXT = 'text-[12.5px] italic leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap break-words';

/** A thought inside a phase: in full while it streams (and until it settles), then
 *  one line — its first line — that opens to all of it. No label. */
function PhaseThought({ unit, folded, open, onToggle }: { unit: ThoughtUnit; folded: boolean; open: boolean; onToggle: (o: boolean) => void }) {
  if (!folded || unit.live) return <div data-testid="thought" data-live={unit.live ? 'true' : 'false'} className={THOUGHT_TEXT}>{unit.text}</div>;
  const first = unit.text.trim().split('\n')[0];
  return (
    <div data-testid="thought" data-folded={open ? 'false' : 'true'}>
      <button
        type="button"
        data-testid="thought-toggle"
        aria-expanded={open}
        onClick={() => onToggle(!open)}
        className={`w-full text-left truncate ${THOUGHT_TEXT.replace('whitespace-pre-wrap break-words', '')} hover:text-[var(--color-text-muted)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 rounded`}
      >
        {first}
      </button>
      {open && <div data-testid="thought-text" className={`${THOUGHT_TEXT} mt-0.5`}>{unit.text}</div>}
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
        <div className={`${INNER_GAP} ml-[22px] space-y-2`}>
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
      <span className="shrink-0 text-[var(--color-text-secondary)]">{file}</span>
    </span>
  );
}

/** One strip: one call, or merged adjacent calls (`×N`). */
export function ToolStripV6({ item }: { item: StripItem }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const step = item.steps[0];
  const v = stripView(step.block);
  const n = item.steps.length;
  const running = step.status === 'pending';
  const elapsed = usePendingToolElapsed(step.block.id, running);
  const stopped = step.status === 'unknown';
  const detail = n > 1 || !!step.result?.outcome;
  const time = running
    ? (elapsed !== null && elapsed >= MIN_SECONDS ? fmt(elapsed) : '')
    : stopped ? tf(t, 'chat.turn.zone.stoppedCall', 'stopped') : '';
  const object = n > 1 && item.objects.length > 1
    ? <span className="truncate min-w-0 text-[var(--color-text-secondary)]">{item.objects.join(', ')}</span>
    : v.dir !== undefined && v.file
      ? <PathObject dir={v.dir} file={v.file} />
      : <span className="truncate min-w-0 text-[var(--color-text-secondary)]">{v.object}</span>;
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
        <span data-testid="strip-tool" className={`w-[10ch] shrink-0 truncate ${running ? 'text-amber-500' : 'text-[var(--color-text-muted)]'}`}>
          {v.tool}{n > 1 ? ` ×${n}` : ''}
        </span>
        {object}
        <span data-testid="strip-time" className={`ml-auto w-[7ch] shrink-0 text-right tabular-nums ${stopped ? 'text-[var(--color-text-dim)]' : 'text-[var(--color-text-secondary)]'}`}>{time}</span>
      </button>
      {open && (
        <div data-testid="strip-detail" className="mt-0.5 mb-1 ml-[calc(10ch+20px)] space-y-0.5 font-mono text-[11.5px] text-[var(--color-text-dim)]">
          {n > 1
            ? item.steps.map((s) => <ToolStripV6 key={`tu-${s.block.id}`} item={{ key: `tu-${s.block.id}`, steps: [s], objects: [] }} />)
            : <div className="whitespace-pre-wrap break-words">{step.result?.outcome}</div>}
        </div>
      )}
    </div>
  );
}

// ── Agents panel ────────────────────────────────────────────────────────────────

const AGENT_RESULT_MAX = 600;

function AgentRow({ step, sessionId, readOnly }: { step: ToolStep; sessionId?: string; readOnly?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const p = step.progress;
  const running = step.status === 'pending' && (p?.status === undefined || p.status === 'running');
  const elapsed = usePendingToolElapsed(step.block.id, running);
  const desc = stripView(step.block).object || stripView(step.block).full;
  const tools = Math.max(step.children?.length ?? 0, p?.toolUses ?? 0);
  const seconds = running ? elapsed ?? undefined : p?.durationMs !== undefined ? Math.round(p.durationMs / 1000) : undefined;
  const meta = [
    seconds !== undefined && seconds >= MIN_SECONDS && fmt(seconds),
    tools > 0 && tf(t, 'chat.turn.zone.tools', tools === 1 ? '{{count}} tool' : '{{count}} tools', { count: tools }),
  ].filter(Boolean).join(' · ');
  // Stopped: the SDK said so, or the turn ended with the agent neither finished nor
  // reported (the user stopped the turn; live progress is gone once streaming ends).
  const stoppedAgent = p?.status === 'stopped' || (!running && step.status === 'unknown' && (!p || p.status === 'running'));
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
    <div data-testid="agent-row" data-status={running ? 'running' : p?.status ?? step.status} {...ANCHOR_PROPS}>
      <div className="flex items-start gap-2 min-w-0">
        <button
          type="button"
          data-testid="agent-toggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="flex-1 flex items-start gap-2 min-w-0 text-left rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span aria-hidden="true" className={`${MARK_COL} text-[12px] text-purple-400`}>{running ? '⟳' : stoppedAgent ? '■' : '✓'}</span>
          <span className="min-w-0 flex-1 break-words text-[12.5px] text-[var(--color-text-secondary)]">{desc}</span>
          {meta && <span className="shrink-0 text-[12px] tabular-nums text-[var(--color-text-dim)]">{meta}</span>}
          {stoppedAgent && <span className="shrink-0 text-[12px] text-[var(--color-text-dim)]">{tf(t, 'chat.turn.zone.stoppedCall', 'stopped')}</span>}
        </button>
        {running && !readOnly && p?.taskId && sessionId && (
          <button
            type="button"
            data-testid="agent-stop"
            disabled={stopping}
            onClick={stop}
            aria-label={tf(t, 'chat.turn.zone.stopAgent', 'Stop this agent')}
            title={tf(t, 'chat.turn.zone.stopAgent', 'Stop this agent')}
            className="shrink-0 px-1 text-[12px] text-[var(--color-text-dim)] hover:text-[var(--color-text)] rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 disabled:opacity-40"
          >
            ✕
          </button>
        )}
      </div>
      {running && p?.lastToolName && (
        <div data-testid="agent-now" className="ml-6 font-mono text-[11.5px] text-purple-400">→ {p.lastToolName}</div>
      )}
      {open && (
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

export function AgentsPanel({ agents, sessionId, readOnly }: { agents: ToolStep[]; sessionId?: string; readOnly?: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const isRunning = (s: ToolStep) => s.status === 'pending' && (s.progress?.status === undefined || s.progress.status === 'running');
  const isStopped = (s: ToolStep) => !isRunning(s)
    && (s.progress?.status === 'stopped' || (s.status === 'unknown' && (!s.progress || s.progress.status === 'running')));
  const running = agents.filter(isRunning).length;
  const stopped = agents.filter(isStopped).length;
  const done = agents.length - running - stopped;
  const meta = [
    running > 0 && `⟳ ${tf(t, 'chat.turn.zone.running', '{{count}} running', { count: running })}`,
    done > 0 && `✓ ${tf(t, 'chat.turn.zone.done', '{{count}} done', { count: done })}`,
    stopped > 0 && `■ ${tf(t, 'chat.turn.zone.stoppedCount', '{{count}} stopped', { count: stopped })}`,
  ].filter((x): x is string => !!x);
  return (
    <section data-testid="agents-panel" className="rounded-[10px] border border-purple-400/25 bg-purple-500/[0.06] px-2.5 py-1.5" {...ANCHOR_PROPS}>
      <div className="flex items-center gap-2 min-w-0">
        <GroupHeader testid="agents-toggle" open={open} onToggle={() => setOpen((o) => !o)} label={tf(t, 'chat.turn.zone.agents', 'Agents')} meta={meta} labelClass="text-purple-400" />
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
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {agents.map((a) => <AgentRow key={`ag-${a.block.id}`} step={a} sessionId={sessionId} readOnly={readOnly} />)}
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
  /** The agent's text (a note, an answer) and a needs block, rendered by the message. */
  renderText: (text: string, key: string) => ReactNode;
  renderNeeds: (zone: Extract<Zone, { kind: 'needs' | 'failure' }>) => ReactNode;
}

type BodyItem =
  | { key: string; thought: ThoughtUnit }
  | { key: string; strips: StripItem[] }
  | { key: string; agents: ToolStep[] };

/** A phase's members as body items in stream order: thoughts, runs of strips, panels. */
function bodyItems(members: PhaseMember[], settled: (id: string) => boolean): BodyItem[] {
  const out: BodyItem[] = [];
  const allSteps = members.flatMap((m) => (m.kind === 'process' ? m.steps : []));
  const batchOf = agentBatches(allSteps);
  const panels = new Map<string, Extract<BodyItem, { agents: ToolStep[] }>>();
  for (const m of members) {
    if (m.kind === 'thought') { out.push({ key: `th-${m.ordinal}`, thought: m }); continue; }
    let run: ToolStep[] = [];
    const flush = () => {
      if (run.length) out.push({ key: `strips-${run[0].block.id}`, strips: mergeStrips(run, settled) });
      run = [];
    };
    for (const s of m.steps) {
      const panelId = batchOf.get(s.block.id);
      if (panelId === undefined) { run.push(s); continue; }
      flush();
      const panel = panels.get(panelId);
      if (panel) panel.agents.push(s);
      else {
        const item = { key: `agents-${panelId}`, agents: [s] };
        panels.set(panelId, item);
        out.push(item);
      }
    }
    flush();
  }
  return out;
}

export function PhaseBody({ members, live, ctx }: { members: PhaseMember[]; live: boolean; ctx: ZoneContext }) {
  const lingering = useLinger(live);
  const active = live || lingering;
  const steps = members.flatMap((m) => (m.kind === 'process' ? m.steps : []));
  const thoughts = members.filter((m): m is ThoughtUnit => m.kind === 'thought');
  const tracked = [
    ...steps.map((s) => ({ id: s.block.id, done: s.status !== 'pending' })),
    // a thought settles once it stopped streaming AND something came after it
    ...thoughts.map((th) => ({ id: `th-${th.ordinal}`, done: !th.live && members[members.length - 1] !== th })),
  ];
  const settled = useSettled(tracked, active);
  const items = bodyItems(members, settled);
  return (
    <div data-testid="phase-body" className="space-y-2">
      {items.map((it) => {
        if ('thought' in it) {
          const k = `th-${it.thought.ordinal}`;
          return (
            <PhaseThought
              key={it.key}
              unit={it.thought}
              folded={!active || settled(k)}
              open={ctx.choice(k) === true}
              onToggle={(o) => ctx.choose(k, o)}
            />
          );
        }
        if ('agents' in it) return <AgentsPanel key={it.key} agents={it.agents} sessionId={ctx.sessionId} readOnly={ctx.readOnly} />;
        return <div key={it.key} className="space-y-[2px]">{it.strips.map((s) => <ToolStripV6 key={s.key} item={s} />)}</div>;
      })}
    </div>
  );
}

export function PhaseGroup({ zone, ctx }: { zone: Extract<Zone, { kind: 'phase' }>; ctx: ZoneContext }) {
  const { t } = useTranslation();
  const lingering = useLinger(zone.live);
  const seconds = useSpanSeconds(ctx.scope, zone.id, zone.live);
  const choice = ctx.choice(zone.id);
  const open = choice ?? (zone.live || lingering);
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
      {open && <div className={`${INNER_GAP} ml-[22px]`}><PhaseBody members={zone.members} live={zone.live} ctx={ctx} /></div>}
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
  const open = mustOpen || (choice ?? (zone.live || lingering || zone.stoppedHere));
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
      {open && zone.body.length > 0 && (
        <div className={`${INNER_GAP} ml-[22px] space-y-3`}>
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
  if (zone.kind === 'text') return <div data-testid="zone-note" className="text-[14px]" {...ANCHOR_PROPS}>{ctx.renderText(zone.text, `${zone.id}-b`)}</div>;
  if (zone.kind === 'needs' || zone.kind === 'failure') return <>{ctx.renderNeeds(zone)}</>;
  return null;
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
              <div data-testid={z.result ? 'turn-result' : z.role === 'restatement' ? 'turn-restatement' : z.role === 'note' ? 'turn-note' : 'turn-answer'} {...ANCHOR_PROPS}>
                {z.result && (
                  <div className="flex items-center gap-2.5 mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
                    {tf(t, 'chat.turn.result', 'Result')}
                    <span className="flex-1 border-t border-[var(--color-border)]" />
                  </div>
                )}
                {ctx.renderText(z.text, `${z.id}-b`)}
              </div>
            );
            break;
          case 'needs': case 'failure': el = ctx.renderNeeds(z); break;
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
          case 'gate': el = <GateRow zone={z} />; break;
          case 'stopped':
            el = (
              <div data-testid="turn-stopped" className="text-[12.5px] text-[var(--color-text-dim)]" {...ANCHOR_PROPS}>
                {z.byUser && !z.unfinished
                  ? tf(t, 'chat.turn.zone.stoppedByYouPlain', 'Stopped by you')
                  : z.byUser
                    ? tf(t, 'chat.turn.zone.stoppedByYou', 'Stopped by you · the last call did not finish')
                    : tf(t, 'chat.turn.zone.stopped', 'Stopped · the last call did not finish')}
              </div>
            );
            break;
          default: el = null;
        }
        return <div key={z.id} data-zone={z.kind} className={gap}>{el}</div>;
      })}
    </>
  );
}
