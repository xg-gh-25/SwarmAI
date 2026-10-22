/**
 * turnZones — the first level of a chat turn (chat turn spec v6,
 * Knowledge/Designs/2026-10-19-chat-turn-display-spec.md), and the tool strips inside it.
 *
 * `turnZones(view, isStreaming)` lays `deriveTurnView`'s units out as the zones the
 * turn reads in: THOUGHTS → restatement → WORKED → … → RESULT, with a pipeline part
 * (`pipelineStart` divider + one zone per started stage / gate) when the turn ran a
 * pipeline. ONE rule decides the first level, for a chat turn and for the work before a
 * run alike: a question / permission / escalation, an unrecovered final failure and the
 * pipeline divider close the current phase. The text the agent wrote between calls
 * stays in the phase, in stream order, as written; so does a thought.
 *
 * Zone kinds:
 * - `thinking`      — the thoughts before anything was written or called (the receipt).
 * - `text`          — the restatement (before the first call), text with no phase open
 *                     (after a question, before any call) or the answer; `result` marks
 *                     the final answer of a turn that had calls, once it has ended.
 * - `phase`         — one WORKING / WORKED group: calls, thoughts and the text between
 *                     them, in stream order.
 * - `needs`         — a question / permission / escalation, in place.
 * - `failure`       — the turn's final call failed and nothing followed it.
 * - `pipelineStart` — `Pipeline started · <profile>`; everything after it is stage zones.
 * - `stage`         — one per started stage (a re-run is the same zone, `rounds` > 1);
 *                     its `body` holds the phases, notes and needs of that stage.
 * - `gate`          — a gate's verdict row; `earlier` lists the verdicts of its earlier rounds.
 * - `stopped`       — the user stopped the turn (`byUser`), or it ended on a call that
 *                     did not finish (`unfinished`); `receipt` (backend turn_stop): the
 *                     calls the Stop found running and how background agents ended.
 *
 * Ids come from ordinals that survive appending and the stream-end reconcile (thought
 * ordinal, text ordinal, process segment id, stage name), so while a turn streams the
 * zone list only grows: no id already shown changes, moves or goes. Work before the
 * first stage row stays where it first appeared (below the divider, or in the stage a
 * resumed run announced). One known exception: a turn that ends before the plan's last
 * stage moves its final answer out of the last stage's body to the RESULT, once, at the
 * end (while streaming it cannot be told from a note the next stage follows).
 *
 * RESULT marks one text: the first text after the turn's last call, once the turn has
 * ended — not when a question / permission / escalation still waits after it, and not
 * in a turn that stopped.
 *
 * Strips: `stripView` is what one call's strip says (the tool's own name and its object,
 * from the backend display object, else the summary); `mergeStrips` merges adjacent
 * calls of one tool with identical input, or adjacent Read / Grep / Glob.
 *
 * @exports turnZones, stripView, mergeStrips, zoneStats, phaseSegments, Zone, PhaseMember, PhaseText, PhaseSegment, StripView, StripItem
 */
import type { InterruptBlock, ProcessUnit, ThoughtUnit, ToolStep, TurnUnit, TurnView } from './turnView';
import { displayCommand, isGateStage, isUnanswered, toolCall } from './turnView';
import type { PipelineDocRef, PipelineStageContent, ToolUseContent, TurnStopContent } from '../../types';

/** What the agent wrote between calls, in the phase, where it was written. */
export interface PhaseText { kind: 'text'; text: string; textOrdinal: number }

export type PhaseMember = ProcessUnit | ThoughtUnit | PhaseText;

export interface StageZone {
  kind: 'stage';
  id: string;
  stage: string;
  status: 'running' | 'done' | 'skipped';
  rounds: number;
  /** The stage now running, while the turn streams. */
  live: boolean;
  /** The stage that follows it in the plan (shown on the live one). */
  next: string | null;
  /** The turn ended while this stage was still running. */
  stoppedHere: boolean;
  /** The turn just ended and is still settling: this was the live stage, and it keeps
   *  its place and stays open until the whole ending lands in one frame. */
  held?: boolean;
  /** ISO times from the backend rows (the latest round's start, its end). */
  startedAt?: string;
  endedAt?: string;
  /** What the completed stage produced: its one-line summary and its documents. */
  summary?: string | null;
  docs?: PipelineDocRef[];
  body: Zone[];
}

export type Zone =
  | { kind: 'thinking'; id: string; thoughts: ThoughtUnit[]; live: boolean }
  | { kind: 'text'; id: string; text: string; role: 'restatement' | 'note' | 'answer'; result: boolean }
  | { kind: 'phase'; id: string; members: PhaseMember[]; live: boolean; held?: boolean }
  | { kind: 'needs'; id: string; block: InterruptBlock }
  | { kind: 'failure'; id: string; step: ToolStep }
  | { kind: 'pipelineStart'; id: string; profile: string | null }
  | { kind: 'stageResult'; id: string; block: PipelineStageContent }
  | StageZone
  | { kind: 'gate'; id: string; stage: string; verdict: string; earlier: string[] }
  | { kind: 'stopped'; id: string; byUser: boolean; unfinished: boolean;
      /** The backend's reason (turn_notice), when it restarted the process or a
       *  newer message took over; absent = the reason is not known. */
      notice?: { reason: string; cause?: string };
      /** The stop receipt (turn_stop): the steps it found running, in stream order,
       *  and the background agents with how they ended. */
      receipt?: StopReceiptView };

/** What the Stopped zone lists under "Stopped by you". */
export interface StopReceiptView {
  at?: string;
  calls: ToolStep[];
  agents: TurnStopContent['agents'];
  omitted?: number;
}

const firstIndex = (u: TurnUnit): number => (u.kind === 'process' ? (u.steps[0]?.index ?? -1) : u.index);

/** A phase cut at the text the agent wrote: the members before the first text, then
 *  one segment per text — the text and the calls / thoughts after it, up to the next. */
export interface PhaseSegment { note: PhaseText; members: PhaseMember[] }
export function phaseSegments(members: PhaseMember[]): { lead: PhaseMember[]; segments: PhaseSegment[] } {
  const lead: PhaseMember[] = [];
  const segments: PhaseSegment[] = [];
  for (const m of members) {
    if (m.kind === 'text') segments.push({ note: m, members: [] });
    else (segments.length ? segments[segments.length - 1].members : lead).push(m);
  }
  return { lead, segments };
}

/** A phase's id: its first member's ordinal key. */
const memberKey = (m: PhaseMember) => (m.kind === 'thought' ? `th-${m.ordinal}` : m.kind === 'text' ? `tx-${m.textOrdinal}` : m.id);

/** A completed stage row's documents (`docs`, else the older single `doc`). */
export function stageDocs(row: PipelineStageContent): PipelineDocRef[] {
  if (row.docs?.length) return row.docs;
  return row.doc ? [row.doc] : [];
}

/**
 * Lays one run of units out as zones (the chat part, or one stage's body): needs and
 * failures close the open phase; calls, thoughts and the text written between calls
 * join it, in stream order. While the turn streams, the latest text after a call
 * joins it too (it cannot be told from a note yet); once the turn ended, the text
 * after the last call is the answer, on the first level.
 */
function layOut(units: TurnUnit[], hasCalls: boolean, resultOrdinal: number | null, restatementAllowed: boolean, isStreaming = false): Zone[] {
  const out: Zone[] = [];
  let phase: Extract<Zone, { kind: 'phase' }> | null = null;
  let seenCall = false;
  const close = () => { phase = null; };
  for (const u of units) {
    if (u.kind === 'process') {
      if (!u.steps.length) continue; // deriveTurnView's empty live row: never an empty WORKING
      seenCall = true;
      if (!phase) { phase = { kind: 'phase', id: `ph-${memberKey(u)}`, members: [], live: false }; out.push(phase); }
      phase.members.push(u);
      if (u.unrecoveredFailure) {
        close();
        out.push({ kind: 'failure', id: `fail-${u.unrecoveredFailure.block.id}`, step: u.unrecoveredFailure });
      }
      continue;
    }
    if (u.kind === 'thought') {
      if (!phase) { phase = { kind: 'phase', id: `ph-${memberKey(u)}`, members: [], live: false }; out.push(phase); }
      phase.members.push(u);
      continue;
    }
    if (u.kind === 'interrupt') {
      close();
      out.push({ kind: 'needs', id: `i-${u.index}`, block: u.block });
      continue;
    }
    if (u.kind === 'stage') continue; // the caller splits on stage rows
    if (u.kind === 'stageResult') {
      close();
      out.push({ kind: 'stageResult', id: `sr-${u.block.stage}-${u.index}`, block: u.block });
      continue;
    }
    // note / landmark / outcome. Text written after a call stays in the open phase.
    if (phase && seenCall && (u.kind !== 'outcome' || isStreaming)) {
      (phase as Extract<Zone, { kind: 'phase' }>).members.push({ kind: 'text', text: u.text, textOrdinal: u.textOrdinal });
      continue;
    }
    close();
    const restatement = restatementAllowed && !seenCall && !out.some((z) => z.kind === 'text');
    const answer = u.kind === 'outcome';
    out.push({
      kind: 'text',
      id: `tx-${u.textOrdinal}`,
      text: u.text,
      role: restatement && !(answer && !hasCalls) ? 'restatement' : answer ? 'answer' : 'note',
      result: answer && u.textOrdinal === resultOrdinal,
    });
  }
  return out;
}

/** Marks the last zone of a zone list live when it is a phase, looking into a live stage. */
function markLive(zones: Zone[], isStreaming: boolean): void {
  if (!isStreaming) return;
  const last = zones[zones.length - 1];
  if (!last) return;
  if (last.kind === 'phase') last.live = true;
  if (last.kind === 'stage' && last.live) markLive(last.body, true);
}

/** The zone markLive marked a moment ago, while the turn settles: it stays open in
 *  place (its own fold timer is not consulted) so the fold, the RESULT and the
 *  ending cards all land together when the settle window closes. Idempotent. */
function markHeld(zones: Zone[]): void {
  const last = zones[zones.length - 1];
  if (!last) return;
  if (last.kind === 'phase') last.held = true;
  if (last.kind === 'stage' && last.held) markHeld(last.body);
}

/**
 * `hold` (default: `isStreaming`) keeps the streaming LAYOUT for the settle window
 * after the turn ends: the answer stays where it streamed and nothing is judged
 * ended yet (RESULT, Stopped, stopped-at). Liveness still follows `isStreaming`, so
 * WORKING reads Worked at once; only the moves wait, and then happen in one frame.
 */
export function turnZones(view: TurnView, isStreaming: boolean, hold: boolean = isStreaming): Zone[] {
  const units = view.units;
  const hasCalls = units.some((u) => u.kind === 'process' && u.steps.length > 0);
  const zones: Zone[] = [];

  // The turn ended on a call that never finished. Without the user's Stop marker this
  // is only judged where the turn kept results (older history has none at all).
  const hasResults = units.some((u) => u.kind === 'process' && u.steps.some((s) => s.resultIndex !== undefined));
  // A call started before a backend turn_notice was lost by that restart / hand-over:
  // the notice accounts for it, so it alone does not make the turn "stopped".
  const notice = view.notice;
  const lost = (s: ToolStep) => !!notice && s.index < notice.index;
  const unfinished = !hold && units.some((u) => u.kind === 'process' && u.steps.some((s) => s.status === 'unknown' && !lost(s)));
  // the notice ended the turn: a hand-over, or a restart with nothing after it
  const lastIndex = Math.max(-1, ...units.map((u) => (u.kind === 'process' ? (u.steps[u.steps.length - 1]?.index ?? -1) : firstIndex(u))));
  const noticeEnds = !hold && !!notice && !unfinished
    && (notice.reason === 'superseded' || lastIndex < notice.index);
  const stopped = !!view.stoppedMarker || (unfinished && hasResults) || noticeEnds;

  // RESULT: the first text after the last call, once the turn ended, with nothing still
  // waiting for the user after it, in a turn that did not stop.
  let resultOrdinal: number | null = null;
  if (hasCalls && !hold && !stopped) {
    const first = units.find((u): u is Extract<TurnUnit, { kind: 'outcome' }> => u.kind === 'outcome');
    const waits = first && units.some((u) => u.kind === 'interrupt' && u.index > first.index && isUnanswered(u.block));
    if (first && !waits) resultOrdinal = first.textOrdinal;
  }

  // THINKING: the thoughts before anything was written or called.
  let lead = 0;
  while (lead < units.length && units[lead].kind === 'thought') lead += 1;
  if (lead > 0) {
    const thoughts = units.slice(0, lead) as ThoughtUnit[];
    zones.push({ kind: 'thinking', id: 'thinking', thoughts, live: isStreaming && lead === units.length && thoughts[lead - 1].live });
  }
  const rest = units.slice(lead);

  // The chat part: everything before the run's first block. A call that straddles it
  // (the run block arrives while run-create runs) is split at the block.
  const start = view.pipelineStart?.index;
  const chat: TurnUnit[] = [];
  const pipe: TurnUnit[] = [];
  for (const u of rest) {
    if (start === undefined) { chat.push(u); continue; }
    if (u.kind === 'process') {
      const before = u.steps.filter((s) => s.index < start);
      const after = u.steps.filter((s) => s.index >= start);
      if (before.length) chat.push(after.length ? { ...u, steps: before, unrecoveredFailure: undefined } : u);
      if (after.length) pipe.push(before.length ? { ...u, id: `${u.id}-p`, steps: after } : u);
      continue;
    }
    (firstIndex(u) < start ? chat : pipe).push(u);
  }
  zones.push(...layOut(chat, hasCalls, resultOrdinal, !view.midWork, hold));

  if (start !== undefined) {
    zones.push({ kind: 'pipelineStart', id: 'pipeline-start', profile: view.pipelineStart?.profile ?? view.run?.profile ?? null });
    zones.push(...pipelineZones(pipe, view, hasCalls, isStreaming, resultOrdinal, hold));
  }

  if (stopped) {
    zones.push({ kind: 'stopped', id: 'stopped', byUser: !!view.stoppedMarker, unfinished,
      ...(noticeEnds && notice ? { notice: { reason: notice.reason, cause: notice.cause } } : {}),
      ...(view.stopReceipt ? { receipt: receiptView(view.stopReceipt, units) } : {}) });
  }
  markLive(zones, isStreaming);
  if (hold && !isStreaming) markHeld(zones);
  return zones;
}

/** The receipt's running calls as this turn's steps (a call the turn does not show —
 *  a sub-agent's own call — is left out), and its background agents. */
function receiptView(r: TurnStopContent, units: TurnUnit[]): StopReceiptView {
  const steps = new Map<string, ToolStep>();
  for (const u of units) if (u.kind === 'process') for (const s of u.steps) steps.set(s.block.id, s);
  const calls = (r.calls ?? []).map((c) => steps.get(c.toolUseId)).filter((s): s is ToolStep => !!s)
    .sort((a, b) => a.index - b.index);
  return { at: r.at, calls, agents: r.agents ?? [], ...(r.omitted ? { omitted: r.omitted } : {}) };
}

function pipelineZones(units: TurnUnit[], view: TurnView, hasCalls: boolean, isStreaming: boolean, resultOrdinal: number | null, hold: boolean = isStreaming): Zone[] {
  const out: Zone[] = [];
  const stages = new Map<string, StageZone>();
  const gates = new Map<string, Extract<Zone, { kind: 'gate' }>>();
  const plan = (view.run?.stages ?? []).filter((s) => !isGateStage(s));
  let current: StageZone | null = null;
  let bodyUnits = new Map<StageZone, TurnUnit[]>();
  // The work before the first stage row. A resumed run announces its stage in progress
  // (pipelineStart.current): that work is the stage's, so the stage opens at once. A
  // fresh run's work stands below the divider, before the first stage, for good.
  const held: TurnUnit[] = [];
  const flushHeld = () => {
    if (held.length) { out.push(...layOut(held, hasCalls, resultOrdinal, false, hold)); held.length = 0; }
  };

  const openStage = (stage: string, row?: PipelineStageContent): StageZone => {
    let z = stages.get(stage);
    if (!z) {
      z = { kind: 'stage', id: `stage-${stage}`, stage, status: 'running', rounds: 1, live: false, next: null, stoppedHere: false, body: [] };
      flushHeld();
      stages.set(stage, z);
      bodyUnits.set(z, []);
      out.push(z);
    } else if (row && row.status === 'in_progress' && z.status !== 'running') {
      z.rounds += 1; // the same stage again: same zone, next round
      z.status = 'running';
      z.endedAt = undefined;
    }
    if (row) {
      if (row.status === 'in_progress') { z.status = 'running'; z.startedAt = row.at; }
      else {
        z.status = row.status === 'skipped' ? 'skipped' : 'done'; z.endedAt = row.at; z.startedAt ??= undefined;
        z.summary = row.summary ?? null;
        z.docs = stageDocs(row);
      }
      const at = plan.indexOf(stage);
      z.next = row.next ?? (at >= 0 ? plan[at + 1] ?? null : null);
    }
    return z;
  };

  const resumed = view.pipelineStart?.current;
  if (resumed && !isGateStage(resumed)) current = openStage(resumed);

  for (const u of units) {
    if (u.kind === 'stage') {
      const { stage } = u.block;
      if (isGateStage(stage)) {
        const verdict = u.block.gate?.verdict ?? (u.block.status === 'completed' ? 'PASS' : '');
        const g = gates.get(stage);
        if (!g) {
          const zone = { kind: 'gate' as const, id: `gate-${stage}`, stage, verdict, earlier: [] as string[] };
          gates.set(stage, zone);
          out.push(zone);
        } else if (verdict) {
          if (g.verdict) g.earlier.push(g.verdict);
          g.verdict = verdict;
        }
        continue;
      }
      current = openStage(stage, u.block);
      continue;
    }
    // The answer goes outside the stages once the plan is through (or the turn ended);
    // while a stage may still follow, text the agent writes stays in the stage it
    // follows, so it never moves out and back in mid-stream.
    if (u.kind === 'outcome') {
      const planDone = !!current && current.status !== 'running' && (plan.length === 0 || plan[plan.length - 1] === current.stage);
      if (!current || planDone || !hold) { flushHeld(); out.push(...layOut([u], hasCalls, resultOrdinal, false)); continue; }
    }
    if (current) bodyUnits.get(current)!.push(u);
    else held.push(u);
  }

  flushHeld();

  // While the turn streams, the stage that took the latest work is live — open, with
  // its `next:` — even when its row already says done (the work between two stages, e.g.
  // a gate's skeptic after PLAN, or text after DELIVER): its mark stays ✓. A turn that
  // ended inside a running stage says `Stopped at X`.
  const last = current;
  for (const z of stages.values()) {
    z.body = layOut(bodyUnits.get(z) ?? [], hasCalls, resultOrdinal, false, hold);
    if (z === last) {
      if (isStreaming) { z.live = true; markLive(z.body, true); }
      else if (hold) { z.held = true; markHeld(z.body); }
      else if (z.status === 'running') z.stoppedHere = true;
    }
    if (!z.live) z.next = null;
  }
  bodyUnits = new Map();
  return out;
}

/** A zone's counts for its header: calls (with the sub-agents' calls), Bash commands. */
export function zoneStats(members: PhaseMember[] | Zone[]): { steps: number; commands: number; thoughts: number } {
  let steps = 0;
  let commands = 0;
  let thoughts = 0;
  const visit = (s: ToolStep) => {
    steps += 1;
    if (s.block.name === 'Bash') commands += 1;
    s.children?.forEach(visit);
  };
  const walk = (m: PhaseMember | Zone) => {
    if (m.kind === 'process') m.steps.forEach(visit);
    else if (m.kind === 'thought') thoughts += 1;
    else if (m.kind === 'phase') m.members.forEach(walk);
    else if (m.kind === 'stage') m.body.forEach(walk);
  };
  (members as Array<PhaseMember | Zone>).forEach(walk);
  return { steps, commands, thoughts };
}

// ── Strips ───────────────────────────────────────────────────────────────────

export interface StripView {
  /** The tool's own name (MCP prefix dropped). */
  tool: string;
  /** What the strip shows: setup removed, heredoc cut, ids shortened. */
  object: string;
  /** The whole object (the hover). */
  full: string;
  /** A path object, split: the directory (home as ~) and the file name. */
  dir?: string;
  file?: string;
  /** Equal for calls with identical input. */
  mergeKey: string;
}

const PATH_TOOLS = new Set(['Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit']);
const HOME = /^\/(?:Users|home)\/[^/\s]+\//;
const UUID = /\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
const LONG_HEX = /\b([0-9a-f]{8})[0-9a-f]{8,}\b/gi;
const HEREDOC = /<<-?\s*(['"]?)([A-Za-z_][\w-]*)\1/;
const cache = new WeakMap<object, StripView>();

const shortIds = (s: string) => s.replace(UUID, '$1').replace(LONG_HEX, '$1');

/** `env [-i] A=1 B=2 ` or plain `A=1 ` in front of a command (not `f=$(…)`: that is code). */
const ENV_PREFIX = /^\s*(?:env(?:\s+-i)?\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"$`]*"|'[^']*'|[^\s;&|$`'"]*)\s+)+(?=[^\s;&|=])/;
/** An interpreter given by absolute path (`/x/.venv/bin/python`) — the row shows its name. */
const ABS_BIN = /(^|\s)~?\/\S*\/bin\/([\w.+-]+)(?=\s|$)/g;
/** A home path anywhere in the command. */
const HOME_ANY = /(^|[\s='"(])\/(?:Users|home)\/[^/\s]+\//g;
/** An interpreter reading its program from stdin: `python3 -I -`, `node -`, `bash -s`. */
const STDIN_PROG = /(?:^|\s)((?:python3?|node|bash|sh|ruby|perl)(?:\s+-[A-Za-z]+)*(?:\s+-)?)\s*$/;
/** A leading plain assignment statement (`B=~/x/backend;`): no code in the value. */
const ASSIGN_STMT = /^\s*([A-Za-z_][A-Za-z0-9_]*)=("[^"$`]*"|'[^']*'|[^\s;&|$`'"]*)\s*(?:;|&&|\n)\s*/;
/** The perl-alarm / timeout wrapper at the start of a LATER segment (`git add x; perl …`). */
const INNER_WRAPPER = /((?:;|&&|\|\||\n)\s*)(?:perl\s+-e\s+'alarm\s+\d+;\s*exec\s+@ARGV'\s+|g?timeout\s+\d+[smh]?\s+)/g;
/** `python3 -I -c "<program>"`: the program, possibly cut by the 200-char summary. */
const PY_C = /(?:^|\s)(python3?(?:\s+-[A-Za-z]+)*\s+-c)\s+(["'])([\s\S]*)$/;

/** Leading `X=value;` statements removed and every later `$X` / `${X}` replaced by
 *  its value, so `B=~/r/backend; $B/.venv/bin/python $B/s.py` reads as the command. */
function inlineAssignments(cmd: string): string {
  const vars = new Map<string, string>();
  let rest = cmd;
  for (let guard = 0; guard < 6; guard += 1) {
    const m = ASSIGN_STMT.exec(rest);
    if (!m) break;
    vars.set(m[1], m[2].replace(/^(["'])(.*)\1$/, '$2'));
    rest = rest.slice(m[0].length);
  }
  if (!vars.size || !rest.trim()) return cmd;
  return rest.replace(/\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?/g, (all, name: string) => vars.get(name) ?? all);
}

/** A `python -c` program shown by its first statement that is not an import. */
function pythonC(cmd: string): string {
  const m = PY_C.exec(cmd);
  if (!m) return cmd;
  const quote = m[2];
  const end = m[3].indexOf(quote);
  const body = end >= 0 ? m[3].slice(0, end) : m[3];
  const first = body.split(/[;\n]/).map((s) => s.trim()).find((s) => s && !/^(?:import|from)\s/.test(s));
  const head = cmd.slice(0, m.index + (m[0].length - m[0].trimStart().length));
  return `${head}${m[1]}${first ? ` · ${first}` : ''}`;
}

/** A Bash command as its strip shows it: the setup, env assignments and wrappers in
 *  front stripped (and a wrapper opening any later segment), leading `X=…;` assignments
 *  inlined, absolute interpreter paths cut to their name, home paths as `~/`, a
 *  `python -c` program and a stdin program (`python3 - <<EOF`) shown by their first
 *  meaningful line. The hover title keeps the raw command. */
function tidyBash(raw: string): string {
  let cmd = displayCommand(raw);
  for (let guard = 0; guard < 4; guard += 1) {
    const next = displayCommand(inlineAssignments(cmd.replace(ENV_PREFIX, '')));
    if (next === cmd) break;
    cmd = next;
  }
  cmd = cmd.replace(INNER_WRAPPER, '$1').replace(ABS_BIN, '$1$2').replace(HOME_ANY, '$1~/');
  const hd = HEREDOC.exec(cmd);
  if (!hd) return pythonC(cmd);
  const head = cmd.slice(0, hd.index);
  const prog = STDIN_PROG.exec(head);
  if (prog) {
    const body = cmd.slice(hd.index + hd[0].length).split('\n').map((l) => l.trim());
    const first = body.find((l) => l && !/^(?:import|from)\s/.test(l) && l !== hd[2] && !l.startsWith('#'));
    return `${head.slice(0, prog.index).trimEnd()}${prog.index > 0 ? ' ' : ''}${prog[1].trim()} <<${hd[2]}${first ? ` · ${first}` : ''}`;
  }
  return `${cmd.slice(0, hd.index + hd[0].length)} …`;
}

export function stripView(block: ToolUseContent): StripView {
  const hit = cache.get(block);
  if (hit) return hit;
  const name = block.name || 'Tool';
  const tool = block.display?.tool || (name.startsWith('mcp__') ? name.split('__').pop() || name : name);
  let full: string;
  let object: string;
  if (name === 'Bash') {
    full = block.display?.object ?? (block.summary || '').replace(/^Running:\s*/, '');
    object = shortIds(tidyBash(full).replace(/\s+/g, ' ').trim());
  } else {
    const call = toolCall(block);
    full = block.display?.object || call.path || call.text;
    object = block.display?.object || call.text;
    if (object === name) object = '';
    object = shortIds(object.replace(/\s+/g, ' ').trim());
  }
  const view: StripView = { tool, object, full, mergeKey: block.display?.key || `${tool}\u0000${full}` };
  if (PATH_TOOLS.has(name) && full.startsWith('/')) {
    const p = full.replace(HOME, '~/');
    const cut = p.lastIndexOf('/');
    view.dir = p.slice(0, cut + 1);
    view.file = p.slice(cut + 1);
    view.object = p;
  }
  cache.set(block, view);
  return view;
}

export interface StripItem {
  /** `tu-<first call id>`: the strip keeps its element while later calls join it. */
  key: string;
  steps: ToolStep[];
  /** A merged Read / Grep / Glob: its distinct objects (file names for paths). */
  objects: string[];
}

const LISTS = new Set(['Read', 'Grep', 'Glob']);

/**
 * Adjacent calls as strips: one tool with identical input merges (`Bash ×3`); adjacent
 * Read / Grep / Glob of one tool merge with their distinct objects. A failed, running,
 * waiting or unfinished call, an Agent, and a call that has not settled yet
 * (`settled(id)` false — it just finished) stay alone.
 */
export function mergeStrips(steps: ToolStep[], settled: (id: string) => boolean): StripItem[] {
  const out: StripItem[] = [];
  const mergeable = (s: ToolStep) => s.status === 'done' && s.block.name !== 'Agent' && s.block.name !== 'Task' && settled(s.block.id);
  const objectOf = (s: ToolStep) => { const v = stripView(s.block); return v.file ?? v.object; };
  for (const s of steps) {
    const prev = out[out.length - 1];
    if (prev && mergeable(s) && prev.steps.every(mergeable)) {
      const a = stripView(prev.steps[0].block);
      const b = stripView(s.block);
      const same = a.tool === b.tool && a.mergeKey === b.mergeKey;
      const list = a.tool === b.tool && LISTS.has(s.block.name);
      if (same || list) {
        prev.steps.push(s);
        const o = objectOf(s);
        if (!prev.objects.includes(o)) prev.objects.push(o);
        continue;
      }
    }
    out.push({ key: `tu-${s.block.id}`, steps: [s], objects: [objectOf(s)] });
  }
  return out;
}

/** An interrupt the user has not answered yet sits in this zone list (the stage holding
 *  it stays open). */
export function hasOpenQuestion(zones: Zone[]): boolean {
  return zones.some((z) => (z.kind === 'needs' && isUnanswered(z.block)) || (z.kind === 'stage' && hasOpenQuestion(z.body)));
}

/** The window event an Agents panel's "Stop all" sends: ChatPage stops the turn (the
 *  same path as the Stop button). */
export const STOP_TURN_EVENT = 'swarm:stop-turn';

/**
 * The Agent calls of one phase, batched into Agents panels: a later Agent joins the
 * panel while any agent in it is still running when it is spawned — still pending, or
 * its result arrives after the new call (they ran at the same time). Other calls between
 * them do not split a panel. Returns, per Agent call id, the id of its panel's first agent.
 */
export function agentBatches(steps: ToolStep[]): Map<string, string> {
  const out = new Map<string, string>();
  let panel: ToolStep[] = [];
  const isAgent = (s: ToolStep) => s.block.name === 'Agent' || s.block.name === 'Task';
  for (const s of steps) {
    if (!isAgent(s)) continue;
    const running = panel.some((a) => a.status === 'pending' || a.status === 'waiting' || (a.resultIndex ?? -1) > s.index
      || (a.status === 'unknown' && a.resultIndex === undefined));
    if (!running) panel = [];
    panel.push(s);
    out.set(s.block.id, panel[0].block.id);
  }
  return out;
}
