/**
 * turnView — the ONE display projection of an assistant turn.
 *
 * `deriveTurnView(content, isStreaming)` turns a message's content blocks into
 * ordered render units. It is a pure function of the store's message (no React,
 * no cache, no second copy of state) so every chat surface that needs to know
 * "what is this turn doing / is that tool done" projects from the same answer.
 *
 * Why it exists: before this module, `groupConsecutiveTools` judged a tool done
 * only when its tool_result was present, while `MergedToolBlock` judged the same
 * no-result state as complete. The backend never persists tool_result blocks, so
 * a reloaded turn could never fold and two modules disagreed about one fact.
 *
 * Unit kinds (time order is always preserved):
 * Stream order is never changed. Only ADJACENT blocks of the same kind are
 * grouped (XG 2026-10-17: "遵守 streaming response 的顺序").
 * - `process`   — a run of ADJACENT tool calls; each call renders as one strip.
 * - `thought`   — a thinking passage, in its place in the stream (never promoted
 *                 out of the work: there is no intent thought, chat display v8);
 *                 adjacent thinking blocks are one passage.
 * - `note`      — text the agent wrote BETWEEN tool calls; a first-level zone between
 *                 two WORKED groups (turnZones).
 * - `landmark`  — a pipeline landmark / gate verdict line. Splits the process into
 *                 segments; like a note it is a first-level text zone that closes the
 *                 phase before it (turnZones). `blocks` marks a gate BLOCK verdict
 *                 (isGateBlock).
 * - `stage`     — a `pipeline_stage` block: a stage the BACKEND saw start or finish
 *                 in run.json (not model text). Splits the process; `turnZones`
 *                 (turnZones.ts) makes one stage zone per started stage.
 * - `outcome`   — text AFTER the last tool call: the answer. Rendered in full, first
 *                 level (a note and the answer are both text zones keyed by
 *                 textOrdinal, so text that turns from answer into note never moves).
 * - `interrupt` — a block that needs the user (question / permission / escalation).
 *                 Never folded; also splits the process.
 *
 * The first level of the turn (THOUGHTS · restatement · WORKED · note · … · pipeline ·
 * RESULT) is laid out by `turnZones` (turnZones.ts, chat turn spec v6) from these
 * units. `toolCall` is a call's real tool name and command; `stepLabel` reads the
 * backend summary (the path a file tool touched).
 *
 * Not units: the backend's display blocks (`pipeline_run`, `pipeline_attention`,
 * `pipeline_summary`, `turn_deliverables`, `turn_changes`) are lifted into
 * `TurnView.run / attention / summary / deliverables / changes`.
 * They have a fixed place in the turn (the ending), not a place in the flow, so they never split a segment or count as "later".
 *
 * The done rule (single source): a tool_use is DONE iff it has a tool_result, OR
 * a later TEXT / thinking block follows it (the model only writes again after its
 * tool calls returned), OR the message is no longer streaming — UNLESS an
 * unanswered permission request / question / escalation follows it, in which
 * case it is WAITING (it has not run; an interrupt is appended after the very
 * call it gates).
 * A later tool_use alone does NOT prove completion — the model may issue several
 * tool calls in parallel in one message, and each stays pending until its own
 * result arrives. Results are persisted in compact form (status + one-line
 * outcome, since 2026-10-18), so a reloaded turn shows failures and outcomes;
 * older turns have none and fall back to the inference above.
 *
 * Sub-agents: a tool_use / tool_result with `parentToolUseId` belongs to the
 * sub-agent run by that Agent step. It is nested under that step (`children`)
 * and ignored by everything else here, so a sub-agent's 40 reads never look
 * like the main agent's steps.
 *
 * @exports deriveTurnView, toolCall, stepLabel, displayCommand, extractNextSteps,
 *          isLandmarkText, isGateStage, isGateBlock, isUnanswered, stripStageHeading,
 *          and the TurnView / TurnUnit / ProcessUnit / ThoughtUnit / Step / ToolStep /
 *          ToolStatus / StepLabel / StepVerb / ToolCallView / NextStepsResult types
 */
import type {
  BackgroundFinishedContent,
  ContentBlock,
  ToolResultContent,
  ToolUseContent,
  AskUserQuestionContent,
  CmdPermissionContent,
  EscalationContent,
  PipelineStageContent,
  PipelineRunContent,
  PipelineAttentionContent,
  PipelineSummaryContent,
  TurnDeliverable,
  TurnChangesContent,
  TurnStopContent,
  SubAgentProgress,
} from '../../types';

/** `waiting` = the tool has not run: an unanswered permission / question gates it.
 *  `unknown` = the turn ENDED on this call (no result, nothing written after it) —
 *  it may have been stopped or cut off, so it is not claimed done. */
export type ToolStatus = 'pending' | 'waiting' | 'unknown' | 'done' | 'error';

export interface ToolStep {
  kind: 'tool';
  block: ToolUseContent;
  index: number;
  status: ToolStatus;
  result?: ToolResultContent;
  /** The content index of its result block, when there is one: an Agent whose result
   *  comes after a later Agent's call ran at the same time as it (one Agents panel). */
  resultIndex?: number;
  /** An Agent step's own sub-agent steps (tool_use blocks whose
   *  `parentToolUseId` is this step), in stream order. */
  children?: ToolStep[];
  /** An Agent step's sub-agent status: live (`subagent_progress`) while
   *  streaming, else the outcome its Agent result kept (`result.subagent`). */
  progress?: SubAgentProgress;
}

/** A segment step. Thinking is not a step: it is a `thought` unit of its own. */
export type Step = ToolStep;

export type InterruptBlock = AskUserQuestionContent | CmdPermissionContent | EscalationContent;

export interface ProcessUnit {
  kind: 'process';
  /** `seg-<ordinal>` — the segment's position among the message's process
   *  segments. Ordinals survive both appending (streaming) and the stream-end
   *  reconcile, which drops live-only tool_result blocks and would shift any
   *  block-index key. */
  id: string;
  /** ADJACENT tool calls, in stream order. */
  steps: Step[];
  /** True only for the last unit of a streaming message — the work in progress. */
  isLive: boolean;
  /** What the live row shows: the pending tool (if any) and the newest finished step. */
  live?: { step?: ToolStep; recent?: ToolStep };
  /** The turn's final tool failed and nothing followed it — shown outside the fold. */
  unrecoveredFailure?: ToolStep;
}

export type TurnUnit =
  | ProcessUnit
  /** Text units (landmark / note / outcome) carry `textOrdinal`: their place among
   *  the turn's text blocks. Unlike `index` it survives the stream-end reconcile
   *  (which drops tool_result and thinking blocks), and one text block keeps it while
   *  it turns from outcome into note or landmark — a stable key.
   *  `blocks`: it states a Gate BLOCK (the run goes back a stage). */
  | { kind: 'landmark'; text: string; index: number; textOrdinal: number; blocks?: boolean }
  /** Text the model wrote between tool calls — shown in the stream, in order. */
  | { kind: 'note'; text: string; index: number; textOrdinal: number }
  | { kind: 'stage'; block: PipelineStageContent; index: number }
  /** A direct run's completed stage: not a pipeline turn, so no stage groups — the
   *  stage's summary and documents stand in the stream where it completed. */
  | { kind: 'stageResult'; block: PipelineStageContent; index: number }
  /** A thinking passage. `ordinal` = its place among the turn's thoughts (a stable
   *  key); `live` = it is still streaming. */
  | ThoughtUnit
  | { kind: 'outcome'; text: string; index: number; textOrdinal: number }
  | { kind: 'interrupt'; block: InterruptBlock; index: number };

export interface ThoughtUnit {
  kind: 'thought';
  text: string;
  index: number;
  ordinal: number;
  live: boolean;
}

/** The pipeline stage now running. */
export interface LiveStage { stage: string; ordinal: number | null }

export interface TurnView {
  units: TurnUnit[];
  /** While streaming a pipeline turn: the stage now running — the `next` of the
   *  latest stage row, or the plan's first stage before any row has arrived. */
  liveStage?: LiveStage;
  /** The pipeline run's alignment (the first `pipeline_run` block), if any. */
  run?: PipelineRunContent;
  /** What the run needs the user to look at (the latest `pipeline_attention`). */
  attention?: PipelineAttentionContent;
  /** The finished run's execution summary (the latest `pipeline_summary`). */
  summary?: PipelineSummaryContent;
  /** The turn's products (`turn_deliverables`), by absolute path, first-seen order. */
  deliverables: TurnDeliverable[];
  /** The turn's code changes (the latest `turn_changes`), if the backend sent them. */
  changes?: TurnChangesContent;
  /** Action lines lifted from a trailing ```next-steps block of the final outcome. */
  nextSteps: string[];
  /** What Copy copies: the outcome text (no process notes, no next-steps block). */
  copyText: string;
  /** The content starts with a tool RESULT: the tail of a compacted / resumed turn.
   *  Nothing in it is how the ask was read, so it has no lead text. */
  midWork: boolean;
  /** Where the pipeline part of the turn begins: the content index of its first
   *  pipeline block (run or stage row) of a non-direct run, and the run's profile.
   *  Everything before it is the chat part (turnZones, chat turn spec v6 §5).
   *  `current`: when that first block is a run block announcing a stage already in
   *  progress (a resumed run), that stage; null for a fresh run or a stage row. It is
   *  fixed by the first block, so where the work before the first stage row goes never
   *  changes while the turn streams. */
  pipelineStart?: { index: number; profile: string | null; current: string | null };
  /** The user pressed Stop in this view (the live `*Stopped*` marker ChatPage
   *  appends; it is not rendered as text), or the backend's stop receipt is here. */
  stoppedMarker?: boolean;
  /** The backend's stop receipt (`turn_stop`, core/turn_stop): what was running when
   *  the user stopped the turn and how background agents ended. */
  stopReceipt?: TurnStopContent;
  /** The latest backend `turn_notice` (why a call did not finish) and its content
   *  index: a call started before it with no result was lost by that event. */
  notice?: { reason: string; cause?: string; index: number };
  /** A turn the CLI started to answer finished background agents: those agents
   *  (`background_finished`, shown as one line at the turn's top). */
  backgroundFinished?: BackgroundFinishedContent['agents'];
}

const INTERRUPT_TYPES = new Set(['ask_user_question', 'cmd_permission_request', 'escalation']);
/** The text block ChatPage appends to the live message when the user presses Stop. */
const STOP_MARKER_RE = /^\s*---\s*\n\s*\*Stopped\*\s*$/;

/** An interrupt the user has not answered yet (so the call before it has not run). */
export function isUnanswered(b: InterruptBlock): boolean {
  if (b.type === 'cmd_permission_request') return !b.decision;
  if (b.type === 'ask_user_question') return !b.answers || Object.keys(b.answers).length === 0;
  return b.status === 'pending';
}

/**
 * Pipeline landmark / gate-verdict syntax (s_autonomous-pipeline Progress Display:
 * `## ✦ ① STAGE`, `## ★ GATE N`, `**PHASE A**`). These are the only mid-turn text
 * the pipeline is allowed to emit, so they stay visible. Derived from the Gate-0
 * probe (Knowledge/Designs/2026-10-17-streaming-audit/interim.py), then tightened
 * at REVIEW (see the comment on the regex) — the probe's loose form over-counts.
 */
// Case-sensitive and word-bounded on purpose: an unanchored /GATE\s*[012]/i also
// matched ordinary prose ("aggregate 1", "delegate 2") and split the fold. The
// mixed-case `Gate N` form the model actually writes ("Gate 1: **PASS**") counts only
// at a LINE START, so a mid-sentence mention ("the Gate 1 skeptic") stays prose; no
// global `i` flag, which would widen the uppercase mid-line branch too.
const LANDMARK_RE = /(^|\n)\s*(## [✦★]|\*\*PHASE [ABC]|🚀 Pipeline|Pipeline (RESUMED|PAUSED)|━━━|(\*\*)?(★\s*)?[Gg]ate [012]\b)|\bGATE [012]\b/;
// A line that LEADS with a circled stage number is a progress line only when it
// carries a second circled number or a stage name ("③ ④ ⑤ 都已完成，进 ⑦ TEST",
// "⑤ BUILD → …"); a lone "① 第一点…" is an ordinary numbered list.
const CIRCLED_LINE_RE = /(^|\n)\s*[①-⑩](?=[^\n]*([①-⑩]|\b(EVALUATE|THINK|PLAN|BUILD|REVIEW|TEST|ADVERSARIAL|DELIVER|REFLECT)\b))/;

/** Is there a gate VERDICT of BLOCK, in the same clause as `gate N` — `Gate 1: BLOCK`,
 *  `**Gate 2:** BLOCK`, `★ GATE 1 · Skeptic → BLOCK`, `Gate 1：BLOCK`, `GATE 1 判定 BLOCK`.
 *  Never a count (`Gate 0 PASS · 0 BLOCK`), never after a PASS in that clause, never a
 *  later clause (`Gate 1 → PASS; Skeptic: BLOCK resolved`). */
// No lookbehind: the macOS 10.15 WKWebView (Safari 15) cannot parse one, and a regex
// literal it cannot parse fails the whole module. The "not a count" check is in code.
const GATE_BLOCK_SPAN_RE = /\bgate\s*[012]\b((?:(?!pass)[^\n;,，；]){0,40}?)\bBLOCK(?:ED)?\b/gi;
export function isGateBlock(text: string): boolean {
  for (const m of text.matchAll(GATE_BLOCK_SPAN_RE)) {
    if (!/\d\s*$/.test(m[1])) return true; // `… 0 BLOCK` is a count
  }
  return false;
}

export function isLandmarkText(text: string): boolean {
  return LANDMARK_RE.test(text) || CIRCLED_LINE_RE.test(text);
}

/** Per-block memo: past text blocks keep their identity while a turn streams, so
 *  the landmark scan runs once per block instead of once per token. */
const landmarkCache = new WeakMap<object, boolean>();
function isLandmarkBlock(block: object, text: string): boolean {
  let v = landmarkCache.get(block);
  if (v === undefined) {
    v = isLandmarkText(text);
    landmarkCache.set(block, v);
  }
  return v;
}

function buildResultMap(content: ContentBlock[]): Map<string, ToolResultContent> {
  const map = new Map<string, ToolResultContent>();
  for (const b of content) {
    if (b.type === 'tool_result') map.set(b.toolUseId, b);
  }
  return map;
}

/** A sub-agent's tool call or result: it nests under its Agent step and is
 *  invisible to the main turn's structure (segments, done rule, outcome). */
const isSubAgentBlock = (b: ContentBlock): boolean =>
  (b.type === 'tool_use' || b.type === 'tool_result') && !!b.parentToolUseId;

/** The step that stands for an Agent called in an earlier turn (its sub-agent's calls
 *  arrived in this message): it nests them, so they are not lost. */
const standInAgent = (id: string): ToolUseContent => ({
  type: 'tool_use', id, name: 'Agent', summary: 'Agent: background agent from an earlier turn',
  category: 'agent', display: { tool: 'Agent', object: 'background agent from an earlier turn', key: `stand-${id}` },
} as ToolUseContent);

/** Backend-written display blocks that are not part of the turn's flow (the run's
 *  alignment, what needs the user, the turn's products): lifted into TurnView
 *  fields, invisible to segments, the done rule and the outcome. */
const DISPLAY_TYPES = new Set(['pipeline_run', 'pipeline_attention', 'pipeline_summary', 'turn_deliverables', 'turn_changes', 'turn_notice', 'background_finished', 'turn_stop']);

/** Derive the display units for one assistant message. Pure.
 *  `progress` is the streaming message's live sub-agent status, by Agent id. */
export function deriveTurnView(
  content: ContentBlock[],
  isStreaming: boolean,
  progress?: Record<string, SubAgentProgress>,
): TurnView {
  const results = buildResultMap(content);
  const resultAt = new Map<string, number>();
  content.forEach((b, i) => { if (b.type === 'tool_result') resultAt.set(b.toolUseId, i); });
  // Sub-agent steps by the Agent tool_use they ran under, in stream order.
  const children = new Map<string, Array<{ block: ToolUseContent; index: number }>>();
  content.forEach((b, index) => {
    if (b.type === 'tool_use' && b.parentToolUseId) {
      const list = children.get(b.parentToolUseId) ?? [];
      list.push({ block: b, index });
      children.set(b.parentToolUseId, list);
    }
  });

  let lastToolIdx = -1;
  const hasResults = content.some((b) => b.type === 'tool_result');
  for (let i = content.length - 1; i >= 0; i -= 1) {
    if (content[i].type === 'tool_use' && !isSubAgentBlock(content[i])) { lastToolIdx = i; break; }
  }
  // Backward scan, per index i:
  //   laterBlock[i]   — some non-tool_result block follows i
  //   laterNonTool[i] — some text/thinking follows i: the model wrote again, so the
  //                     tool calls before it returned
  //   gatedAfter[i]   — the NEXT text/thinking/interrupt after i is an UNANSWERED
  //                     interrupt (the call before it is waiting on the user)
  const laterBlock: boolean[] = new Array(content.length).fill(false);
  const laterNonTool: boolean[] = new Array(content.length).fill(false);
  const gatedAfter: boolean[] = new Array(content.length).fill(false);
  let seen = false;
  let seenNonTool = false;
  let gate = false;
  for (let i = content.length - 1; i >= 0; i -= 1) {
    laterBlock[i] = seen;
    laterNonTool[i] = seenNonTool;
    gatedAfter[i] = gate;
    const b = content[i];
    if (isSubAgentBlock(b) || DISPLAY_TYPES.has(b.type)) continue;
    // The Stop marker is written by the app when the user stops the turn, not by the
    // model: it proves nothing returned, so the call before it stays unknown (stopped).
    if (b.type === 'text' && STOP_MARKER_RE.test(b.text || '')) continue;
    if (b.type !== 'tool_result') seen = true;
    if (b.type === 'text' || b.type === 'thinking') { seenNonTool = true; gate = false; }
    if (INTERRUPT_TYPES.has(b.type)) gate = isUnanswered(b as InterruptBlock);
  }

  // A user Stop interrupts the calls it found running; the CLI then hands each an
  // error result written for the model ("doesn't want to proceed…"). That is a stop,
  // not a failure. The backend's receipt names those calls; until it arrives (live,
  // right after Stop) the app's marker stands for the last call alone.
  let stopReceipt: TurnStopContent | undefined;
  for (const b of content) if (b.type === 'turn_stop') stopReceipt = b;
  // a call that was still waiting for approval never ran: it is not "stopped"
  const stoppedIds = new Set((stopReceipt?.calls ?? []).filter((c) => c.ran !== false).map((c) => c.toolUseId));
  const markerOnly = !stopReceipt && content.some((b) => b.type === 'text' && STOP_MARKER_RE.test(b.text || ''));
  const cutByStop = (id: string, index: number, result?: ToolResultContent) =>
    (!result || result.isError) && (stoppedIds.has(id) || (markerOnly && index === lastToolIdx && !!result));

  const units: TurnUnit[] = [];
  let seg: ProcessUnit | null = null;
  let ordinal = 0;
  // Stream order is never changed: a process segment is a run of ADJACENT
  // tool calls; any text, thought, stage row or interrupt closes it.
  const openSeg = (): ProcessUnit => {
    if (!seg) {
      seg = { kind: 'process', id: `seg-${ordinal}`, steps: [], isLive: false };
      ordinal += 1;
      units.push(seg);
    }
    return seg;
  };

  let run: PipelineRunContent | undefined;
  // A direct run (profile `direct`: a code change made outside the pipeline, in its own
  // worktree) is not a pipeline: its stage and run blocks do not make this a pipeline
  // turn, so it shows as a chat turn (XG 2026-10-19). The run's latest profile decides:
  // a run moved up to a pipeline profile shows as one from then on.
  const runProfile = new Map<string, string>();
  for (const b of content) {
    if ((b.type === 'pipeline_stage' || b.type === 'pipeline_run') && typeof b.profile === 'string') {
      runProfile.set(b.runId, b.profile.toLowerCase());
    }
  }
  const isRunBlock = (b: ContentBlock): b is PipelineStageContent | PipelineRunContent =>
    b.type === 'pipeline_stage' || b.type === 'pipeline_run';
  const isPipelineBlock = (b: ContentBlock) => isRunBlock(b) && runProfile.get(b.runId) !== 'direct';
  // A pipeline turn: its stage headings are said by the backend rows (stripStageHeading).
  const pipelineTurn = content.some(isPipelineBlock);
  const pipelineAt = content.findIndex(isPipelineBlock);
  let pipelineStart: TurnView['pipelineStart'];
  if (pipelineAt >= 0) {
    const first = content[pipelineAt] as PipelineStageContent | PipelineRunContent;
    pipelineStart = {
      index: pipelineAt,
      profile: runProfile.get(first.runId) ?? first.profile ?? null,
      current: first.type === 'pipeline_run' ? first.current ?? null : null,
    };
  }
  let stoppedMarker = false;
  let notice: TurnView['notice'];
  let backgroundFinished: TurnView['backgroundFinished'];
  let thoughts = 0;
  let texts = 0;
  let attention: PipelineAttentionContent | undefined;
  let summary: PipelineSummaryContent | undefined;
  const deliverables: TurnDeliverable[] = [];
  let changes: TurnChangesContent | undefined;
  // One tool step (and its sub-agent children) into the open process segment. `stand`:
  // a stand-in Agent step for sub-agent calls whose Agent was called in an earlier turn
  // (a background agent still running): running while the turn streams, else done.
  const pushToolStep = (block: ToolUseContent, index: number, stand = false) => {
    const cut = !stand && !isStreaming && cutByStop(block.id, index, results.get(block.id));
    // a call the Stop interrupted: its error result is the CLI's note to the model
    const result = cut ? undefined : results.get(block.id);
    const status: ToolStatus = stand ? (isStreaming ? 'pending' : 'done') : cut ? 'unknown' : result?.isError
      ? 'error'
      : result
        ? 'done'
        : gatedAfter[index]
          ? 'waiting'
          : laterNonTool[index]
            ? 'done'
            : isStreaming
              ? 'pending'
              // The turn ended with no result for it and nothing written after:
              // stopped or cut off. Where results are recorded (every returned
              // call has one) that holds for EVERY such call — parallel calls end
              // together. Older history kept no results at all; there a later call
              // is the only sign the earlier ones returned, so only the last one
              // is unknown.
              : hasResults || index === lastToolIdx
                ? 'unknown'
                : 'done';
    const step: ToolStep = { kind: 'tool', block, index, status, result };
    if (resultAt.has(block.id)) step.resultIndex = resultAt.get(block.id);
    const subs = children.get(block.id);
    // The sub-agent's outcome: live progress while streaming, else what its
    // Agent result kept (status, steps, duration, summary — persisted).
    const live = isStreaming ? progress?.[block.id] : undefined;
    const kept = result?.subagent ? { ...result.subagent, parentToolUseId: block.id } : undefined;
    // a background launch stays `background` while its agent reports running live
    const subOutcome = kept?.status === 'background' && (!live || live.status === 'running')
      ? { ...live, ...kept } : live ?? kept;
    const subCut = subOutcome?.status === 'failed' || subOutcome?.status === 'stopped';
    if (subs) {
      // A sub-agent step is done when its own result came back; without one it
      // shares its Agent's fate (running while it runs, cut off if it was).
      step.children = subs.map(({ block: sb, index: si }) => {
        const r = results.get(sb.id);
        const s: ToolStatus = r?.isError ? 'error' : r ? 'done'
          : status === 'pending' || status === 'unknown' || status === 'waiting' ? status
            : subCut ? 'unknown' : 'done';
        return { kind: 'tool', block: sb, index: si, status: s, result: r };
      });
    }
    if (subOutcome) step.progress = subOutcome;
    openSeg().steps.push(step);
  };
  // The Agent calls of this message; a sub-agent call under any other id ran under an
  // Agent of an earlier turn and gets a stand-in step where its first call is.
  // (any tool_use id here counts: a sub-agent's own Agent nests its calls itself)
  const agentIds = new Set(content.filter((b) => b.type === 'tool_use').map((b) => (b as ToolUseContent).id));
  const standIns = new Set<string>();
  content.forEach((block, index) => {
    if (block.type === 'tool_result') return; // consumed by its tool_use
    if (isSubAgentBlock(block)) {
      if (block.type === 'tool_use' && block.parentToolUseId && !agentIds.has(block.parentToolUseId) && !standIns.has(block.parentToolUseId)) {
        const parent = block.parentToolUseId;
        standIns.add(parent);
        pushToolStep(standInAgent(parent), index, true);
      }
      return; // nested under its Agent step
    }
    if (isRunBlock(block) && !isPipelineBlock(block)) {
      // A direct run (above) has no stage groups, but what it produced is shown: each
      // completed stage's summary and documents (its end summary is lifted below, for
      // every profile).
      if (block.type === 'pipeline_stage' && block.status === 'completed' && !isGateStage(block.stage)
               && (block.summary || block.docs?.length || block.doc)) {
        seg = null;
        units.push({ kind: 'stageResult', block, index });
      }
      return;
    }
    if (block.type === 'pipeline_run') {
      // …unless the turn itself starts that stage (the block is written from the state
      // AFTER the call that announced it, so `current` may be a stage this very call
      // starts — its own start row opens it).
      const startsHere = content.some((b) => b.type === 'pipeline_stage' && b.status === 'in_progress'
        && b.runId === block.runId && b.stage === block.current);
      const stages = Array.isArray(block.stages) ? block.stages : [];
      if (!run && block.current && !startsHere) {
        // A run announced mid-way (a resumed run): its current stage is running from
        // here — open its row now (an end block later in the turn updates it).
        seg = null;
        units.push({ kind: 'stage', index, block: {
          type: 'pipeline_stage', runId: block.runId, stage: block.current, status: 'in_progress',
          ordinal: null, position: stages.indexOf(block.current) >= 0 ? stages.indexOf(block.current) + 1 : null,
          total: stages.length, profile: block.profile, next: null, nextOrdinal: null,
          gate: null, summary: null, at: block.at,
        } });
      }
      // The first block of the run is its alignment; a later one of the same run (the
      // profile was set or changed) brings the plan: its checklist, profile, stages.
      if (!run) run = block;
      else if (run.runId === block.runId) {
        run = {
          ...run,
          profile: block.profile ?? run.profile,
          stages: stages.length ? stages : run.stages,
          checklist: block.checklist?.length ? block.checklist : run.checklist,
        };
      }
      return;
    }
    if (block.type === 'pipeline_attention') { attention = block; return; }
    if (block.type === 'pipeline_summary') { summary = block; return; }
    if (block.type === 'background_finished') {
      if (Array.isArray(block.agents) && block.agents.length) backgroundFinished = [...(backgroundFinished ?? []), ...block.agents];
      return;
    }
    if (block.type === 'turn_stop') return; // stopReceipt, read above
    if (block.type === 'turn_notice') {
      notice = { reason: String(block.reason || ''), cause: block.cause, index };
      return;
    }
    if (block.type === 'turn_changes') {
      if (Array.isArray(block.files) && block.files.length) changes = block;
      return;
    }
    if (block.type === 'turn_deliverables') {
      for (const f of block.files ?? []) {
        if (f?.absolutePath && !deliverables.some((d) => d.absolutePath === f.absolutePath)) deliverables.push(f);
      }
      return;
    }
    if (block.type === 'pipeline_stage') {
      seg = null;
      units.push({ kind: 'stage', block, index });
      return;
    }
    if (INTERRUPT_TYPES.has(block.type)) {
      seg = null;
      units.push({ kind: 'interrupt', block: block as InterruptBlock, index });
      return;
    }
    if (block.type === 'text') {
      const raw = block.text || '';
      if (!raw.trim()) return;
      if (STOP_MARKER_RE.test(raw)) { stoppedMarker = true; return; }
      const textOrdinal = texts;
      texts += 1;
      const text = pipelineTurn ? stripStageHeading(raw) : raw;
      if (!text.trim()) return; // only a stage heading: the backend row says it
      seg = null;
      if (index > lastToolIdx) {
        units.push({ kind: 'outcome', text, index, textOrdinal });
      } else if (isLandmarkBlock(block, text)) {
        const unit: TurnUnit = { kind: 'landmark', text, index, textOrdinal };
        if (isGateBlock(text)) unit.blocks = true;
        units.push(unit);
      } else {
        // What the model says between tool calls stays in the stream, in order.
        units.push({ kind: 'note', text, index, textOrdinal });
      }
      return;
    }
    if (block.type === 'thinking') {
      const text = block.thinking || '';
      if (!text.trim()) return;
      seg = null;
      // Two thinking blocks with nothing between them are one passage of thought
      // (the model thought on before writing or calling anything): one row.
      const prev = units[units.length - 1];
      if (prev?.kind === 'thought') {
        prev.text = `${prev.text.trimEnd()}\n\n${text.trimStart()}`;
        return;
      }
      units.push({ kind: 'thought', text, index, ordinal: thoughts, live: false });
      thoughts += 1;
      return;
    }
    if (block.type === 'tool_use') { pushToolStep(block, index); return; }
  });

  if (!isStreaming && lastToolIdx >= 0 && !laterBlock[lastToolIdx]) {
    for (const u of units) {
      if (u.kind !== 'process') continue;
      const tail = u.steps.find((st): st is ToolStep => st.kind === 'tool' && st.index === lastToolIdx);
      if (tail && tail.status === 'error') u.unrecoveredFailure = tail;
    }
  }

  // A turn whose content starts with a tool RESULT is the tail of a compacted /
  // resumed one: its opening thought is mid-work, not how the ask was read.
  const midWork = content[0]?.type === 'tool_result';

  let liveStage: LiveStage | undefined;
  if (isStreaming) {
    let last = units[units.length - 1];
    // A thought still streaming is itself the live item (it carries the spinner).
    // Nothing yet: no live row — the streaming bee is the turn's one spinner, and the
    // first thing on screen is what the agent says, never an empty "Working" card.
    if (last?.kind === 'thought') {
      last.live = true;
    } else if (last && (last.kind === 'stage' || last.kind === 'note')) {
      // A stage just finished (or the model just wrote a note) and nothing has
      // run yet: still show a live row, so the user sees work continuing and the
      // stage now running is named immediately.
      last = { kind: 'process', id: `seg-${ordinal}`, steps: [], isLive: true, live: {} };
      units.push(last);
    }
    if (last && last.kind === 'process') {
      last.isLive = true;
      const pending = [...last.steps].reverse().find(
        (s): s is ToolStep => s.kind === 'tool' && s.status === 'pending',
      );
      // The newest step that already finished, so the row always says what was
      // just done — not only a bare spinner between two tool calls.
      const recent = [...last.steps].reverse().find(
        (s): s is ToolStep => s.kind === 'tool' && s.status !== 'pending',
      );
      last.live = { step: pending, recent };
    }
    const stageAt = units.map((u) => u.kind).lastIndexOf('stage');
    const prevStage = stageAt >= 0 ? (units[stageAt] as Extract<TurnUnit, { kind: 'stage' }>) : undefined;
    // A Gate BLOCK after the latest stage row ("Gate 1: BLOCK" → back to plan) means
    // that row's `next` no longer holds — show no stage rather than a stale one. Any
    // other landmark (the "✦ ⑦ TEST" line the pipeline writes after each row) keeps it.
    const landmarkAfter = units.slice(stageAt + 1).some((u) => u.kind === 'landmark' && !!u.blocks);
    // The latest stage (not gate) row that started and has not ended is the one
    // running: its stage zone is live (turnZones).
    const open = [...units].reverse().find((u): u is Extract<TurnUnit, { kind: 'stage' }> =>
      u.kind === 'stage' && !isGateStage(u.block.stage));
    if (open && open.block.status === 'in_progress') {
      liveStage = { stage: open.block.stage, ordinal: open.block.ordinal };
    } else if (prevStage?.block.next && !landmarkAfter) {
      liveStage = { stage: prevStage.block.next, ordinal: prevStage.block.nextOrdinal };
    } else if (!prevStage && run?.stages?.length && !landmarkAfter) {
      // Before the first stage row: the run's current stage (a resumed run), else the
      // plan's first stage, is running, so its work sits under that stage from the
      // start and folds in place when the row lands.
      liveStage = { stage: run.current ?? run.stages[0], ordinal: null };
    }
  }

  let nextSteps: string[] = [];
  const final = units[units.length - 1];
  if (final && final.kind === 'outcome') {
    const r = extractNextSteps(final.text, isStreaming);
    nextSteps = r.steps;
    if (r.text.trim()) final.text = r.text;
    else units.pop();
  }
  const copyText = units
    .filter((u): u is Extract<TurnUnit, { kind: 'outcome' }> => u.kind === 'outcome')
    .map((u) => u.text)
    .join('\n\n');
  return { units, nextSteps, copyText, run, attention, summary, deliverables, changes, liveStage, midWork, pipelineStart,
    // a receipt for background agents only (no turn ran) does not stop this turn
    stoppedMarker: stoppedMarker || (!!stopReceipt && stopReceipt.turn !== false),
    ...(stopReceipt ? { stopReceipt } : {}), notice, backgroundFinished };
}

// ── Step labels ─────────────────────────────────────────────────────────────
//
// The backend `summary` is a prose string (backend/core/tool_summarizer.py,
// e.g. `Running: <cmd>`, `Reading <path>`). Other code parses it by prefix too,
// so it is NOT changed here — this is a read-only adapter that turns it into a
// verb + object for display. An unrecognised shape falls back to the raw text.

export type StepVerb =
  | 'read' | 'edit' | 'search' | 'run' | 'list' | 'fetch' | 'websearch'
  | 'loadTools' | 'skill' | 'todo' | 'agent' | 'mcp' | 'tool';

export interface StepLabel {
  verb: StepVerb;
  /** Short human object: a basename, a quoted pattern, a command head. */
  object: string;
  /** Full file path when the step targets a file (click-to-open). */
  path?: string;
  /** The original summary — the third disclosure layer. */
  raw: string;
  /** An `edit` that created / replaced the whole file (`cat > f`, `tee f`), not an
   *  in-place edit: the row says Wrote, not Edited. */
  created?: boolean;
}

const basename = (p: string): string => {
  const clean = p.replace(/[/\\]+$/, '');
  const i = Math.max(clean.lastIndexOf('/'), clean.lastIndexOf('\\'));
  return i >= 0 ? clean.slice(i + 1) || clean : clean;
};

/** Leading shell setup that says nothing about what the command does. */
// Each setup segment may carry redirections (`2>/dev/null`) and `|| cd <fallback>`
// chains, and ends at `&&`, `;` or a newline — shapes measured in real summaries.
const SETUP_RE = /^\s*(?:cd|export\s+[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*')?|source|set\s+-)[^\n;&|]*(?:\|\|\s*(?:cd[^\n;&|]*|exit\b[^\n;&|]*|true\b|\{[^}]*\}))*\s*(?:&&|;|\n)\s*/;
/** What is left when the (200-char-truncated) summary held nothing but setup. */
const SETUP_ONLY_RE = /^\s*(?:cd|export|source|set)\b/;
const WRAPPER_RE = /^\s*(?:perl\s+-e\s+'alarm\s+\d+;\s*exec\s+@ARGV'\s+|g?timeout\s+\d+[smh]?\s+)/;
/** A bare assignment (`R=run_x;`) or an env prefix (`FOO=1 cmd`): never the subject. */
const ASSIGN_RE = /^\s*[A-Za-z_][A-Za-z0-9_]*=(?:"[^"]*"|'[^']*'|[^\s;&|]*)(?:\s*(?:&&|;|\n)\s*|\s+)/;
/** `LOG=$(ls -t … | head -1)`: the command inside the substitution is the subject. */
const ASSIGN_SUBST_RE = /^\s*[A-Za-z_][A-Za-z0-9_]*=\$\(/;
/** Code inside a setup segment: substitution, process substitution, a brace block. */
const SETUP_CODE_RE = /\$\(|`|<\(|>\(|\{/;
/** A probe that runs before the real command (`uptime; …`) and says nothing about it. */
const PROBE_RE = /^\s*(?:uptime|date|pwd|true|clear|sleep\s+\d+)\b[^\n;&|]*(?:&&|;|\n)\s*/;

/** The command without its leading setup and wrappers — ONE loop for display and
 *  detection. Display (`toolCall`) strips only cd / export / source / set and the
 *  perl-alarm / timeout wrapper, so the row is still the real command. Detection
 *  (`labelBash`: what a step counts as) also drops assignments and probes, so
 *  `R=x; cat > a.py` counts as an edit. */
function stripBashSetup(cmd: string, detect: boolean): string {
  let rest = cmd;
  for (let guard = 0; guard < 8; guard += 1) {
    const setup = SETUP_RE.exec(rest);
    // Display: a "setup" segment that runs code (`source <(curl …)`, `export X=$(…)`,
    // `cd x || { curl …; }`) is part of what ran — the row keeps it.
    if (!detect && setup && SETUP_CODE_RE.test(setup[0])) break;
    let next = rest.replace(SETUP_RE, '');
    if (detect) next = next.replace(ASSIGN_SUBST_RE, '').replace(ASSIGN_RE, '').replace(PROBE_RE, '');
    next = next.replace(WRAPPER_RE, '');
    if (next === rest) break;
    rest = next;
  }
  return rest;
}
/** A Bash command as a strip shows it: leading cd / export / source / set setup and a
 *  perl-alarm / timeout wrapper removed (the display mode of stripBashSetup). */
export function displayCommand(cmd: string): string {
  return stripBashSetup(cmd, false);
}

/** The command split at every UNQUOTED `&&` / `||` / `|` / `;` / newline. */
function splitSegments(cmd: string): string[] {
  const out: string[] = [];
  let quote: string | null = null;
  let start = 0;
  for (let i = 0; i < cmd.length; i += 1) {
    const c = cmd[i];
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") { quote = c; continue; }
    const two = (c === '&' && cmd[i + 1] === '&') || (c === '|' && cmd[i + 1] === '|');
    if (c === '\n' || c === ';' || c === '|' || two) {
      out.push(cmd.slice(start, i).trim());
      if (two) i += 1;
      start = i + 1;
    }
  }
  out.push(cmd.slice(start).trim());
  return out.filter(Boolean);
}

/** Commands that only read. A Bash step is relabelled (Read / Search / List) ONLY
 *  when every segment of it is one of these, so `grep -l x | xargs rm` stays "Run". */
const READ_ONLY_HEADS = new Set([
  'cat', 'head', 'tail', 'less', 'sed', 'wc', 'grep', 'rg', 'ugrep', 'egrep', 'fgrep',
  'ls', 'tree', 'find', 'sort', 'uniq', 'cut', 'tr', 'jq', 'nl', 'diff', 'stat', 'file',
  'echo', 'printf', 'basename', 'dirname', 'column', 'true', 'pwd', 'which', 'realpath',
]);
const FIND_WRITES = new Set(['-delete', '-exec', '-execdir', '-ok', '-okdir', '-fprint', '-fprintf', '-fls']);

/** The segment with every quoted string blanked, so shell operators inside quotes
 *  (`grep ">" a`) are not read as operators. */
const unquoted = (segment: string): string => segment.replace(/"[^"]*"|'[^']*'/g, '""');

function isReadOnlySegment(segment: string): boolean {
  if (/`|\$\(/.test(segment)) return false; // command substitution runs anything
  const bare = unquoted(segment);
  // Process substitution (`<(…)`, `>(…)`) runs a command; a lone `&` starts another
  // one in the background (`cat a & rm -rf b`). Fail closed: not a read.
  if (/[<>]\(/.test(bare)) return false;
  if (/&/.test(bare.replace(/&&|\d?>&\d?|&>>?/g, ''))) return false;
  // Every redirection, attached or not (`a>b`, `> b`, `2>> log`), writes unless it is
  // `/dev/null` or a descriptor copy (`2>&1`).
  for (const m of bare.matchAll(/(?:\d|&)?>>?\s*(&?)(\S*)/g)) {
    if (m[1] === '&') continue;
    if (m[2] !== '/dev/null') return false;
  }
  const tokens = tokenize(segment);
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  if (!READ_ONLY_HEADS.has(head)) return false;
  if (head === 'sed' && tokens.some((t) => /^-[a-zA-Z]*i/.test(t) || t.startsWith('--in-place'))) return false;
  // sed's own write / execute commands (`w file`, `W file`, `e cmd`, `s///w file`).
  if (head === 'sed' && tokens.slice(1).some((t) => !t.startsWith('-')
    && (/(?:^|[;}\s/])[wWe](?:\s|$)/.test(t) || /^s(.).*?\1.*?\1[gpiImM0-9]*[wWe]/.test(t)))) return false;
  if (head === 'sort' && tokens.some((t) => t === '-o' || t.startsWith('--output'))) return false;
  if (head === 'tree' && tokens.some((t) => t === '-o' || t.startsWith('--output'))) return false;
  if ((head === 'rg' || head === 'ugrep') && tokens.some((t) => t === '--pre' || t.startsWith('--pre='))) return false;
  if (head === 'find' && tokens.some((t) => FIND_WRITES.has(t))) return false;
  return true;
}

function tokenize(segment: string): string[] {
  return shellTokens(segment).map((t) => t.text);
}

/** Tokens with whether each was quoted: a quoted `>` is an argument, not a redirection. */
function shellTokens(segment: string): Array<{ text: string; quoted: boolean }> {
  const out: Array<{ text: string; quoted: boolean }> = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(segment))) out.push({ text: m[1] ?? m[2] ?? m[3], quoted: m[3] === undefined });
  return out;
}

const looksLikePath = (t: string): boolean => !t.startsWith('-') && /[./]/.test(t) && !/^\d+(,\d+)?p?$/.test(t);

/** The absolute directory a command `cd`s into first, if it says so plainly. */
function leadingCd(cmd: string): string | undefined {
  const m = /^\s*cd\s+(?:"([^"]+)"|'([^']+)'|([^\s;&|]+))/.exec(cmd);
  const dir = m ? (m[1] ?? m[2] ?? m[3]) : undefined;
  return dir && dir.startsWith('/') ? dir.replace(/\/+$/, '') : undefined;
}

/** A file argument as a full path: absolute as is, relative only under a known
 *  absolute `cd`. Anything else (`~`, `$VAR`, `..`, no cd) gets no link — never a guess. */
function resolveBashPath(file: string, cwd: string | undefined): string | undefined {
  if (/[$`*?]/.test(file) || file.startsWith('~')) return undefined;
  if (file.startsWith('/')) return file;
  if (!cwd || file.split('/').includes('..')) return undefined;
  return `${cwd}/${file.replace(/^\.\//, '')}`;
}

function labelBash(cmd: string): Pick<StepLabel, 'verb' | 'object' | 'path' | 'created'> {
  const rest = stripBashSetup(cmd, true);
  if (SETUP_ONLY_RE.test(rest)) return { verb: 'run', object: '…' };
  const segments = splitSegments(rest);
  const segment = segments[0] ?? '';
  // Redirections (`2>/dev/null`, `>out`, `2>&1`) are not arguments — unquoted ones.
  const tokens = shellTokens(segment).filter((tok) => tok.quoted || (!/^\d?>/.test(tok.text) && !/^&?>/.test(tok.text))).map((tok) => tok.text);
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  const args = tokens.slice(1);
  const nonFlag = args.filter((a) => !a.startsWith('-'));

  if (!segments.every(isReadOnlySegment)) {
    // A write-only command (`cat > a.py <<EOF`, `tee a.md`) changed that file: count it.
    const write = /^(?:cat|tee|printf|echo)$/.test(head) ? writeTarget(segment) : undefined;
    if (write) {
      const full = resolveBashPath(write, leadingCd(cmd));
      return full ? { verb: 'edit', object: basename(write), path: full, created: true } : { verb: 'edit', object: basename(write), created: true };
    }
    // An in-place edit (`sed -i … f`, `perl -pi -e … f`) of ONE file, in a command of
    // one segment: that file changed. Anything else that may write stays a run.
    const inPlace = (head === 'sed' && args.some((a) => /^-[a-zA-Z]*i/.test(a) || a.startsWith('--in-place')))
      || (head === 'perl' && args.some((a) => /^-(?![MmI])[a-zA-Z]*i/.test(a)) && args.some((a) => /^-(?![MmI])[a-zA-Z]*p/.test(a)));
    if (inPlace && segments.length === 1) {
      // The files are the arguments after the script: sed's script is the first
      // non-flag argument unless it came with -e / -f; perl's comes with -e. Exactly
      // one file → that file was edited; none or several → a run (never a guessed name).
      const scripted = args.some((a) => a === '-e' || a === '-f' || /^-[a-zA-Z]*e$/.test(a));
      // (`-i ''` — the BSD form — gives an empty argument: not a file.)
      const operands = args.filter((a, k) => a !== '' && !a.startsWith('-') && !(k > 0 && /^(?:-e|-f|-[a-zA-Z]*e)$/.test(args[k - 1])));
      const files = head === 'sed' && !scripted ? operands.slice(1) : operands;
      if (files.length === 1) {
        const full = resolveBashPath(files[0], leadingCd(cmd));
        return full ? { verb: 'edit', object: basename(files[0]), path: full } : { verb: 'edit', object: basename(files[0]) };
      }
    }
    // Something in it may write: never dress it up as a read. Scripts and known
    // tools keep their short name; anything else shows the command itself.
    // The backend program and its subcommand (`"$SWARM_BACKEND_BIN" <subcommand> <verb>`,
    // `python-backend py x.py`): the subcommand and the word after it say what ran.
    if (head === '$SWARM_BACKEND_BIN' || head === '${SWARM_BACKEND_BIN}' || head === 'python-backend') {
      const [sub, next] = args;
      if (sub) {
        const second = next && !next.startsWith('-') ? ` ${looksLikePath(next) ? basename(next) : next}` : '';
        return { verb: 'run', object: `${sub}${second}` };
      }
    }
    if (['python', 'python3', 'node', 'bash', 'sh', 'perl', 'ruby', 'deno'].includes(head)) {
      const script = nonFlag.find((a) => /\.[a-z]{1,4}$/i.test(a));
      if (script) {
        // A subcommand right after the script (`tool.py land`) says what ran.
        const sub = args[args.indexOf(script) + 1];
        const subcommand = sub && /^[a-z][\w:-]*$/i.test(sub) && !looksLikePath(sub) ? ` ${sub}` : '';
        return { verb: 'run', object: `${basename(script)}${subcommand}` };
      }
    }
    if (['git', 'npm', 'npx', 'pnpm', 'yarn', 'uv', 'cargo', 'go', 'gh'].includes(head)) {
      return { verb: 'run', object: [head, ...args.slice(0, 2)].join(' ') };
    }
    const whole = rest.replace(/\s+/g, ' ').trim();
    return { verb: 'run', object: whole.length > 60 ? `${whole.slice(0, 57)}…` : whole || cmd.slice(0, 60) };
  }

  if (['cat', 'head', 'tail', 'less', 'sed', 'wc'].includes(head)) {
    const file = [...args].reverse().find(looksLikePath);
    if (file) {
      const full = resolveBashPath(file, leadingCd(cmd));
      return full ? { verb: 'read', object: basename(file), path: full } : { verb: 'read', object: basename(file) };
    }
  }
  if (['grep', 'rg', 'ugrep', 'egrep', 'fgrep'].includes(head)) {
    const pattern = nonFlag.find((a) => !a.startsWith('--')) ?? nonFlag[0];
    if (pattern) return { verb: 'search', object: `"${pattern}"` };
  }
  if (head === 'find') {
    // `find <dir> -name <pattern>` searches for files: the pattern is the object.
    const at = args.findIndex((a) => a === '-name' || a === '-iname' || a === '-path');
    const pattern = at >= 0 ? args[at + 1] : undefined;
    // The start directory is find's FIRST argument when it is not an expression.
    const dir = args[0] && !args[0].startsWith('-') ? args[0] : '.';
    return { verb: 'search', object: pattern ? `"${pattern}"` : basename(dir) };
  }
  if (['ls', 'tree'].includes(head)) {
    const dir = nonFlag.find(looksLikePath) ?? nonFlag[0] ?? '.';
    return { verb: 'list', object: basename(dir) };
  }
  const shown = segment.length > 60 ? `${segment.slice(0, 57)}…` : segment;
  return { verb: 'run', object: shown || cmd.slice(0, 60) };
}

/** `cat > a.py <<EOF` / `tee a.md`: the file a write-only command creates. */
function writeTarget(segment: string): string | undefined {
  const tokens = tokenize(segment);
  for (let i = 1; i < tokens.length; i += 1) {
    const m = /^>>?(.*)$/.exec(tokens[i]);
    if (m) { const t = m[1] || tokens[i + 1]; return t && looksLikePath(t) ? t : undefined; }
  }
  if (tokens[0] === 'tee') return tokens.slice(1).find((t) => !t.startsWith('-') && looksLikePath(t));
  return undefined;
}

/** The real call, for display (chat display v7.12): the tool's own name and its own
 *  command / arguments — never a translated verb or kind. A Bash command loses only
 *  what never says what it does: leading cd / export / source / set setup and a
 *  perl-alarm / timeout wrapper (the same SETUP_RE / WRAPPER_RE loop labelBash uses).
 *  File tools name their path (`path` makes it a link). Pure; cached per block. */
export interface ToolCallView {
  tool: string;
  text: string;
  path?: string;
}

/** The verb backend tool_summarizer puts in front of a non-Bash summary (its formats:
 *  `Reading p` · `Writing to p` · `Searching web for q` · `Searching for q` ·
 *  `Fetching u` · `Listing p` · `Loading tools: …` · `Using skill: s` ·
 *  `Writing N todos` · `Agent: …` / `Agent (name): …` · `MCP: tool — ctx`).
 *  Case-sensitive: the summarizer's own words, never a word of the arguments. */
const SUMMARY_VERB_RE = /^(?:Reading|Writing to|Searching web for|Searching for|Fetching|Listing|Loading tools:|Using skill:|Writing(?= \d+ todos?\b)|Agent(?: \([^)]*\))?:|MCP:)\s*/;
const HOME_RE = /^\/(?:Users|home)\/[^/\s]+\//;

const callCache = new WeakMap<object, ToolCallView>();

export function toolCall(block: ToolUseContent): ToolCallView {
  const hit = callCache.get(block);
  if (hit) return hit;
  const tool = block.name || 'Tool';
  const summary = (block.summary || '').trim();
  let view: ToolCallView;
  if (tool === 'Bash') {
    const rest = stripBashSetup(summary.replace(/^Running:\s*/, ''), false);
    // A summary cut inside its setup leaves only the setup: say so, do not show it. (A
    // setup kept because it runs code still ends in a separator: it is shown.)
    const cut = SETUP_ONLY_RE.test(rest) && !SETUP_RE.test(rest);
    const text = cut ? '…' : rest.replace(/\s+/g, ' ').trim();
    // No command at all (an empty or bare "Running:" summary): the tool name alone.
    view = { tool, text: text || tool };
  } else {
    const label = stepLabel(block);
    // `Using <name>` says nothing but the name; `<name>: ctx` is the generic form.
    let text = summary === `Using ${tool}` ? ''
      : summary.startsWith(`${tool}: `) ? summary.slice(tool.length + 2)
        : summary.replace(SUMMARY_VERB_RE, '');
    // The home directory is the one prefix every path repeats: shown as ~ (the full
    // path stays the link's target and tooltip).
    // `mcp__srv__email_search` + `MCP: email_search — q`: the name is already on the row.
    if (tool.startsWith('mcp__')) {
      const short = tool.split('__').pop() ?? '';
      if (text.startsWith(`${short} — `)) text = text.slice(short.length + 3);
      else if (text.trim() === short) text = '';
    }
    text = text.trim().replace(HOME_RE, '~/') || tool;
    view = label.path ? { tool, text, path: label.path } : { tool, text };
  }
  callCache.set(block, view);
  return view;
}

/** backend tool_summarizer MAX_SUMMARY_LENGTH — a summary this long was cut. */
const SUMMARY_CAP = 200;
/** A file target, unless the backend truncated the summary through the path. */
const fileTarget = (raw: string) => (v: string): Partial<StepLabel> =>
  raw.length >= SUMMARY_CAP ? { object: `${basename(v)}…` } : { object: basename(v), path: v };

const PREFIXES: Array<[string, StepVerb, (v: string, raw: string) => Partial<StepLabel>]> = [
  ['Reading ', 'read', (v, raw) => fileTarget(raw)(v)],
  ['Writing to ', 'edit', (v, raw) => fileTarget(raw)(v)],
  ['Searching web for ', 'websearch', (v) => ({ object: v })],
  ['Searching for ', 'search', (v) => ({ object: v })],
  ['Fetching ', 'fetch', (v) => ({ object: v.replace(/^https?:\/\//, '').replace(/\/$/, '') })],
  ['Loading tools: ', 'loadTools', (v) => ({ object: v })],
  ['Using skill: ', 'skill', (v) => ({ object: v })],
  ['Listing ', 'list', (v) => ({ object: basename(v) })],
  ['MCP: ', 'mcp', (v) => ({ object: v })],
];

/** Per-block memo: a tool_use block never changes after it streams in, so its
 *  label is computed once, not on every render of a long turn. */
const labelCache = new WeakMap<object, StepLabel>();

/** Turn a tool_use's backend summary into a display verb + object. Pure. */
export function stepLabel(block: ToolUseContent): StepLabel {
  const hit = labelCache.get(block);
  if (hit && hit.raw === (block.summary || block.name || '')) return hit;
  const label = computeStepLabel(block);
  labelCache.set(block, label);
  return label;
}

function computeStepLabel(block: ToolUseContent): StepLabel {
  const raw = block.summary || block.name || '';
  if (raw.startsWith('Running: ')) {
    const bash = labelBash(raw.slice('Running: '.length));
    // A summary cut at the cap may have cut through the path: no link then.
    if (raw.length >= SUMMARY_CAP) delete bash.path;
    return { ...bash, raw };
  }
  for (const [prefix, verb, build] of PREFIXES) {
    if (raw.startsWith(prefix)) {
      const value = raw.slice(prefix.length).trim();
      if (value) return { verb, object: value, raw, ...build(value, raw) };
    }
  }
  const todo = /^Writing (\d+) todos/.exec(raw);
  if (todo) return { verb: 'todo', object: `${todo[1]} todos`, raw };
  const agent = /^Agent(?: \([^)]*\))?: (.+)$/s.exec(raw);
  if (agent) return { verb: 'agent', object: agent[1], raw };
  return { verb: 'tool', object: raw, raw };
}

// ── Next steps ──────────────────────────────────────────────────────────────
//
// STEERING #7 format: a desktop reply may END with a fenced ```next-steps block,
// one action per line. It is lifted out of the rendered answer and shown as
// clickable chips. While streaming, any trailing fragment that could still
// become that fence is hidden so the raw block never flashes.

const NEXT_FENCE = '```next-steps';
const MAX_NEXT_STEPS = 3;
/** A chip must show exactly what one click sends — so lines are short... */
const MAX_STEP_CHARS = 120;
/** High-confidence unresolved references that force the user to scroll up and
 * map internal working context. The model contract is the primary fix (AGENT
 * R18); this parser rejects ONLY shapes whose whole line is opaque. It must not
 * infer semantics from a keyword appearing inside an otherwise named action:
 * "发布第 2 版 OAuth schema" and "Run 4 targeted tests" are self-contained. */
const ZH_DEICTIC_RE = /(?:这个|那个|这些|那些|上述|上面|前面|前述|该项|此项|该|此)(.*)$/i;
const ZH_GENERIC_TAIL_RE = /^\s*(?:(?:问题|方案|修改|改动|内容|选项|步骤|工作|任务|版本|计划|做法|run)\s*)?(?:(?:再|并|继续|做|修复|修改|改|合并|部署|重跑|处理|确认|执行|采用|应用|完成|一下|吧|即可|了)\s*)*[。.!！]?\s*$/i;
const EN_DEICTIC_RE = /\b(?:this|that|the\s+above|the\s+previous|above|previous)\b(.*)$/i;
const EN_GENERIC_TAIL_RE = /^\s*(?:(?:issue|change|plan|option|item|run|version|approach|task|work|step)\s*)?(?:(?:now|next|instead|please|again)\s*)*[.!]?\s*$/i;
const OPAQUE_ZH_ORDINAL_RE = /^(?:请\s*)?(?:(?:处理|继续|完成|修复|修改|合并|查看|确认|执行|部署|重跑|做)\s*)?第\s*(?:\d+|[一二三四五六七八九十百]+)\s*(?:项|个|步|条|种|版)\s*(?:(?:选|选择)\s*(?:方案|选项)?\s*[A-Z](?:\s*(?:\/|还是|或)\s*[A-Z])?)?\s*(?:吧|即可)?[。.!！]?$/i;
const OPAQUE_ZH_CHOICE_RE = /^(?:请\s*)?(?:选|选择|采用|确认|用)\s*(?:方案|选项)?\s*[A-Z](?:\s*(?:\/|还是|或)\s*[A-Z])?\s*(?:吧|即可)?[。.!！]?$/i;
const OPAQUE_DEICTIC_RUN_RE = /^(?:这个|那个|上述|上面|前面|前述|该|此)\s*Run\s*#?\s*\d+\b/i;
const OPAQUE_RUN_RE = /^(?:请\s*|please\s+)?(?:(?:继续|处理|修复|修改|合并|查看|确认|执行|部署|重跑|continue|handle|process|fix|merge|review|confirm|execute|deploy|rerun)\s+)?Run\s*#?\s*\d+\s*(?:again|next|再|并|继续|修|改|合|处理|确认)?\s*[。.!！]?$/i;
const OPAQUE_EN_ITEM_RE = /^(?:please\s+)?(?:(?:do|handle|process|continue|complete|fix|review|use|apply|merge|deploy|rerun)\s+)?(?:item|step|option|version)\s*#?\s*\d+\s*(?:again|next|please)?[.!]?$/i;
const OPAQUE_EN_CHOICE_RE = /^(?:please\s+)?(?:choose|pick|select|use)\s+(?:(?:option|approach|plan)\s+)?[A-Z](?:\s*(?:\/|or)\s*[A-Z])?\s*(?:please)?[.!]?$/i;

function hasOpaqueDeicticTail(step: string, marker: RegExp, genericTail: RegExp): boolean {
  const match = marker.exec(step);
  return match !== null && genericTail.test(match[1] ?? '');
}

export function isSelfContainedNextStep(step: string): boolean {
  return !(
    hasOpaqueDeicticTail(step, ZH_DEICTIC_RE, ZH_GENERIC_TAIL_RE)
    || hasOpaqueDeicticTail(step, EN_DEICTIC_RE, EN_GENERIC_TAIL_RE)
    || OPAQUE_ZH_ORDINAL_RE.test(step)
    || OPAQUE_ZH_CHOICE_RE.test(step)
    || OPAQUE_DEICTIC_RUN_RE.test(step)
    || OPAQUE_RUN_RE.test(step)
    || OPAQUE_EN_ITEM_RE.test(step)
    || OPAQUE_EN_CHOICE_RE.test(step)
  );
}
/** ...and carry nothing invisible that could hide text: C0/C1 controls, private
 *  use, line/paragraph separators, soft hyphen, bidi marks/overrides/isolates,
 *  zero-width space / word joiners / BOM, TAG characters and variation selectors
 *  1-15 + supplementary ones — so the chip shows exactly what one click sends
 *  (RP65). Deliberately KEPT: ZWNJ/ZWJ (U+200C/D) and VS16 (U+FE0F), which real
 *  emoji and scripts need. No `\p{Cn}`: unassigned-ness depends on the WebKit's
 *  Unicode version, so it would delete newer emoji on older macOS. */
// eslint-disable-next-line no-misleading-character-class -- these variation selectors are listed precisely to be stripped
const INVISIBLE_RE = /[\p{Cc}\p{Co}\p{Zl}\p{Zp}\u00AD\u061C\u180E\u200B\u200E\u200F\u202A-\u202E\u2060-\u2064\u2066-\u206F\uFEFF\uFE00-\uFE0E\u{E0000}-\u{E007F}\u{E0100}-\u{E01EF}]/gu;

export interface NextStepsResult {
  /** The answer text with the block removed (trailing whitespace trimmed). */
  text: string;
  /** Up to 3 action lines (empty when there is no block, or while streaming). */
  steps: string[];
}

function parseStepLines(body: string): string[] {
  return body
    .split('\n')
    .map((l) => l.replace(INVISIBLE_RE, '').replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').trim())
    .filter((l) => l.length > 0 && l !== '```' && isSelfContainedNextStep(l))
    .slice(0, MAX_NEXT_STEPS)
    .map((l) => (l.length > MAX_STEP_CHARS ? `${l.slice(0, MAX_STEP_CHARS - 1)}…` : l));
}

/** Split a trailing ```next-steps block off an answer. Pure. */
export function extractNextSteps(text: string, isStreaming: boolean): NextStepsResult {
  const start = text.lastIndexOf(NEXT_FENCE);
  if (start >= 0 && (start === 0 || text[start - 1] === '\n')) {
    const after = text.slice(start + NEXT_FENCE.length);
    const close = after.indexOf('\n```');
    const tail = close >= 0 ? after.slice(close + 4) : '';
    if (close < 0 || tail.trim() === '') {
      const before = text.slice(0, start).replace(/\s+$/, '');
      if (isStreaming) return { text: before, steps: [] };
      const body = close >= 0 ? after.slice(0, close) : after;
      return { text: before, steps: parseStepLines(body) };
    }
    return { text, steps: [] };
  }
  if (isStreaming) {
    const nl = text.lastIndexOf('\n');
    const lastLine = text.slice(nl + 1);
    if (lastLine.startsWith('`') && NEXT_FENCE.startsWith(lastLine)) {
      return { text: text.slice(0, nl + 1).replace(/\s+$/, ''), steps: [] };
    }
  }
  return { text, steps: [] };
}

/** A gate row's stage name: gate0 / gate1 / gate2. */
export const isGateStage = (stage: string): boolean => /^gate\d$/i.test(stage);

/** A model-written stage heading ("## ✦ ⑤ BUILD", "## ★ Gate 0: SUPPORTED"). */
const STAGE_HEADING_RE = /^\s*#{1,6}\s*[✦★]/;
const GATE_HEADING_RE = /\bgate\s*[012]\b/i; // the pipeline writes "★ GATE 1: …" too
/** The verdict right after "Gate N" (":", "—", "**" between) is a pass — never "NO-GO". */
const GATE_PASS_RE = /\b[Gg]ate\s*[012][\s:：*—–-]*(PASS|SUPPORTED|PROCEED|GO)\b(?!-)/i;

/** In a pipeline turn, a text's leading stage heading and its "→ …" lines are what
 *  the backend row already says: they are removed, the rest of the text is kept. A
 *  gate heading that is not a pass keeps the whole text (its reasons). Returns ''
 *  when nothing is left. Text with no such heading is returned unchanged. */
export function stripStageHeading(text: string): string {
  const lines = text.split('\n');
  let i = 0;
  while (i < lines.length && !lines[i].trim()) i += 1;
  if (i >= lines.length || !STAGE_HEADING_RE.test(lines[i])) return text;
  if (GATE_HEADING_RE.test(lines[i]) && !GATE_PASS_RE.test(lines[i])) return text;
  i += 1;
  while (i < lines.length && (/^\s*→/.test(lines[i]) || !lines[i].trim())) i += 1;
  return lines.slice(i).join('\n');
}

// ── Notes ────────────────────────────────────────────────────────────────────

export type NoteUnit = Extract<TurnUnit, { kind: 'note' }>;


