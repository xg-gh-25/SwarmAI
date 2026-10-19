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
 *                 out of the work: there is no intent thought, chat display v8).
 * - `note`      — text the agent wrote BETWEEN tool calls; a line of its WORKED block.
 * - `landmark`  — a pipeline landmark / gate verdict line. Splits the process into
 *                 segments; inside a WORKED block it is one of its lines. `blocks`
 *                 marks a gate BLOCK verdict (isGateBlock).
 * - `stage`     — a `pipeline_stage` block: a stage the BACKEND saw start or finish
 *                 in run.json (not model text). Stays visible and splits the
 *                 process; `sectionUnits` / `pipelineChecklist` lay it onto the
 *                 run's checklist line.
 * - `outcome`   — text AFTER the last tool call: the answer. Rendered in full, first
 *                 level. While it streams, the block above it stays live (groupWork
 *                 `holdLiveAboveAnswer`), so that block does not fold and re-open if a
 *                 tool call follows and the text becomes a note.
 * - `interrupt` — a block that needs the user (question / permission / escalation).
 *                 Never folded; also splits the process.
 *
 * A second, pure pass — `groupWork` — gathers everything between two first-level
 * items (thoughts, notes, landmarks, tool calls) into one WORKING / WORKED block
 * (chat display v8). `toolCall` is a call's real tool name and command; `toolRow` is
 * what its row says (Codex-style verb + real object, `explore` for a read-only
 * read / search / list).
 *
 * Not units: the backend's display blocks (`pipeline_run`, `pipeline_attention`,
 * `pipeline_summary`, `turn_deliverables`) are lifted into `TurnView.run / attention /
 * summary / deliverables`.
 * They have a fixed place in the turn (the pipeline block's header / before the
 * result / after it),
 * not a place in the flow, so they never split a segment or count as "later".
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
 * @exports deriveTurnView, groupWork, toolCall, toolRow, stepLabel,
 *          extractNextSteps, isLandmarkText, sectionUnits, pipelineChecklist, stageCounts,
 *          isGateStage, isGateBlock,
 *          and the TurnView / TurnUnit / ProcessUnit / Step / ToolStep /
 *          ToolStatus / StepLabel / StepVerb / ToolCallView / ToolRowView / WorkGroup /
 *          WorkItem / ChecklistLine / NextStepsResult types
 */
import type {
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
  /** Action lines lifted from a trailing ```next-steps block of the final outcome. */
  nextSteps: string[];
  /** What Copy copies: the outcome text (no process notes, no next-steps block). */
  copyText: string;
  /** The content starts with a tool RESULT: the tail of a compacted / resumed turn.
   *  Nothing in it is how the ask was read, so it has no lead text. */
  midWork: boolean;
}

const INTERRUPT_TYPES = new Set(['ask_user_question', 'cmd_permission_request', 'escalation']);

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

/** Backend-written display blocks that are not part of the turn's flow (the run's
 *  alignment, what needs the user, the turn's products): lifted into TurnView
 *  fields, invisible to segments, the done rule and the outcome. */
const DISPLAY_TYPES = new Set(['pipeline_run', 'pipeline_attention', 'pipeline_summary', 'turn_deliverables']);

/** Derive the display units for one assistant message. Pure.
 *  `progress` is the streaming message's live sub-agent status, by Agent id. */
export function deriveTurnView(
  content: ContentBlock[],
  isStreaming: boolean,
  progress?: Record<string, SubAgentProgress>,
): TurnView {
  const results = buildResultMap(content);
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
    if (b.type !== 'tool_result') seen = true;
    if (b.type === 'text' || b.type === 'thinking') { seenNonTool = true; gate = false; }
    if (INTERRUPT_TYPES.has(b.type)) gate = isUnanswered(b as InterruptBlock);
  }

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
  // A pipeline turn: its stage headings are said by the backend rows (stripStageHeading).
  const pipelineTurn = content.some((b) => b.type === 'pipeline_stage' || b.type === 'pipeline_run');
  let thoughts = 0;
  let texts = 0;
  let attention: PipelineAttentionContent | undefined;
  let summary: PipelineSummaryContent | undefined;
  const deliverables: TurnDeliverable[] = [];
  content.forEach((block, index) => {
    if (block.type === 'tool_result') return; // consumed by its tool_use
    if (isSubAgentBlock(block)) return; // nested under its Agent step below
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
      units.push({ kind: 'thought', text, index, ordinal: thoughts, live: false });
      thoughts += 1;
      return;
    }
    if (block.type === 'tool_use') {
      const result = results.get(block.id);
      const status: ToolStatus = result?.isError
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
      const subs = children.get(block.id);
      // The sub-agent's outcome: live progress while streaming, else what its
      // Agent result kept (status, steps, duration, summary — persisted).
      const live = isStreaming ? progress?.[block.id] : undefined;
      const kept = result?.subagent ? { ...result.subagent, parentToolUseId: block.id } : undefined;
      const subOutcome = live ?? kept;
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
    }
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
    if (last?.kind === 'thought') {
      last.live = true;
    } else if (!last) {
      units.push({ kind: 'process', id: 'seg-live', steps: [], isLive: true, live: {} });
    } else if (last.kind === 'stage' || last.kind === 'note') {
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
    // running: its own section is live (sectionUnits).
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
  return { units, nextSteps, copyText, run, attention, summary, deliverables, liveStage, midWork };
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
    if (['python', 'python3', 'node', 'bash', 'sh', 'perl', 'ruby', 'deno'].includes(head)) {
      const script = nonFlag.find((a) => /\.[a-z]{1,4}$/i.test(a));
      if (script) {
        // A subcommand right after the script (`artifact_cli.py run-update`) says what ran.
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

/** What a tool row says (chat display v8, XG 2026-10-19 — Codex-style verbs): a
 *  verb + the call's REAL object, and the full command for the running / opened row.
 *  `explore`: a read, search or list that only reads — such rows merge into one
 *  "Explored" line once settled. Built on the one classifier (stepLabel / labelBash),
 *  so a command that may write is never an exploration row. */
export interface ToolRowView {
  verb: string;
  object: string;
  path?: string;
  explore: boolean;
  command: string;
}

const ROW_VERB: Partial<Record<StepVerb, string>> = {
  read: 'Read', search: 'Search', list: 'List', run: 'Ran', agent: 'Agent',
};
const rowCache = new WeakMap<object, ToolRowView>();

export function toolRow(block: ToolUseContent): ToolRowView {
  const hit = rowCache.get(block);
  if (hit) return hit;
  const label = stepLabel(block);
  const call = toolCall(block);
  const name = block.name || 'Tool';
  let verb = ROW_VERB[label.verb] ?? name;
  if (label.verb === 'edit') verb = name === 'Write' || label.created ? 'Wrote' : 'Edited';
  // An MCP call: the tool's own name (the server prefix is the tooltip), then its argument.
  if (label.verb === 'mcp') verb = name.split('__').pop() || name;
  const byCall = label.verb === 'tool' || label.verb === 'mcp' || label.object === name;
  // A run says its program and subcommand ("Ran git log -6", "Ran artifact_cli.py
  // run-update"); the whole command is the row's second line while it runs and once
  // the row is opened (XG spec, chat display v8).
  const view: ToolRowView = {
    verb,
    object: byCall ? (call.text === name ? '' : call.text) : label.object,
    explore: label.verb === 'read' || label.verb === 'search' || label.verb === 'list',
    command: call.text === name ? '' : call.text,
  };
  if (label.path) view.path = label.path;
  rowCache.set(block, view);
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
    .filter((l) => l.length > 0 && l !== '```')
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

// ── Display rows ────────────────────────────────────────────────────────────

/** One row of an expanded segment: a step, or ADJACENT finished reads of one file. */
export type StepRowItem =
  | { kind: 'step'; step: Step }
  | { kind: 'reads'; steps: ToolStep[] };

const readKey = (s: Step): string | null => {
  if (s.kind !== 'tool' || s.status !== 'done') return null;
  const label = stepLabel(s.block);
  return label.verb === 'read' ? (label.path ?? label.object) : null;
};

/** Merge runs of adjacent finished reads of the SAME file into one row. Order is
 *  kept; anything in between (another file, thinking, a failure) breaks the run. */
export function groupSteps(steps: Step[]): StepRowItem[] {
  const out: StepRowItem[] = [];
  let runKey: string | null = null;
  for (const step of steps) {
    const key = readKey(step);
    const prev = out[out.length - 1];
    if (key !== null && key === runKey && prev) {
      if (prev.kind === 'reads') prev.steps.push(step as ToolStep);
      else out[out.length - 1] = { kind: 'reads', steps: [(prev.step as ToolStep), step as ToolStep] };
      continue;
    }
    out.push({ kind: 'step', step });
    runKey = key;
  }
  return out;
}

// ── What changed ────────────────────────────────────────────────────────────

/** Files the given steps wrote (Write/Edit), in first-touch order, with the
 *  sub-agent steps nested under an Agent step included. Summary says EFFECT. */
export function changedFiles(steps: Step[]): string[] {
  const out: string[] = [];
  const visit = (s: ToolStep) => {
    const label = stepLabel(s.block);
    if (label.verb === 'edit' && label.path && s.status !== 'error' && !out.includes(label.path)) out.push(label.path);
    s.children?.forEach(visit);
  };
  for (const s of steps) if (s.kind === 'tool') visit(s);
  return out;
}

/** Files changed anywhere in the turn's process units. */
export function turnChangedFiles(units: TurnUnit[]): string[] {
  const out: string[] = [];
  for (const u of units) {
    if (u.kind !== 'process') continue;
    for (const p of changedFiles(u.steps)) if (!out.includes(p)) out.push(p);
  }
  return out;
}

// ── Work blocks (chat display v8) ──────────────────────────────────────────
//
// A turn has two levels. FIRST level: the restatement (plain text), WORKING / WORKED
// blocks, the pipeline block, interrupts and the result. SECOND level, inside a block (or an opened
// stage), in stream order: thoughts, notes the model wrote between tool calls,
// landmarks and tool calls. So a block is EVERYTHING between two first-level items —
// a thought or a note never makes a first-level row of its own. Only the last block of
// a streaming turn is live (the WORKING card); a finished one folds to WORKED.

export interface WorkGroup {
  kind: 'group';
  /** `grp-th-<ordinal>` when it starts with a thought, else `grp-<first process
   *  seg-<n>>`, else `grp-txt-<ordinal>`: never changes while the block grows (a
   *  thinking-only block keeps its id when its first tool call lands), and survives
   *  the stream-end reconcile. */
  id: string;
  /** Its second-level items, in stream order. */
  members: WorkMember[];
  /** The last block of a streaming turn. */
  live: boolean;
  /** Has at least one tool call. */
  hasTools: boolean;
}

export type LandmarkUnit = Extract<TurnUnit, { kind: 'landmark' }>;
/** A text member: both carry `textOrdinal`. */
export type TextUnit = NoteUnit | LandmarkUnit;
export type WorkMember = ThoughtUnit | ProcessUnit | NoteUnit | LandmarkUnit;

/** groupWork's output: blocks and the first-level units between them (a thought is
 *  always inside a block). */
export type WorkItem = WorkGroup | Exclude<TurnUnit, ProcessUnit | ThoughtUnit>;

export interface GroupWorkOptions {
  /** A note that stays first level: the turn's opening restatement. */
  exclude?: TurnUnit;
  /** Units that joined a block from elsewhere (the thoughts before the restatement,
   *  moved into the first stage once the turn settles): they do not count for the
   *  block's id, so the block keeps the id it streamed with. */
  joined?: ReadonlySet<TurnUnit>;
  /** Merge thoughts-only blocks right before `exclude` into the next block (default
   *  true). False while the turn streams. */
  mergeLead?: boolean;
  /** While streaming, a block with tool calls right before the answer-so-far (the
   *  last unit, an outcome) stays live — open, not folding. The answer stays first
   *  level so the final answer never moves when the turn ends; if a tool call
   *  follows, the text joins this still-open block as a note (XG 2026-10-18, option
   *  B: the rare note moves, the common answer does not). */
  holdLiveAboveAnswer?: boolean;
}

export function groupWork(units: TurnUnit[], isStreaming: boolean, opts: GroupWorkOptions = {}): WorkItem[] {
  const out: WorkItem[] = [];
  let cur: WorkGroup | null = null;
  const member = (u: TurnUnit): u is WorkMember => {
    if (u === opts.exclude) return false;
    if (u.kind === 'thought') return true;
    return u.kind === 'process' || u.kind === 'note' || u.kind === 'landmark';
  };
  for (const u of units) {
    if (member(u)) {
      if (!cur) {
        cur = { kind: 'group', id: '', members: [], live: false, hasTools: false };
        out.push(cur);
      }
      cur.members.push(u);
      if (u.kind === 'process' && u.steps.length > 0) cur.hasTools = true;
      continue;
    }
    cur = null;
    out.push(u as Exclude<TurnUnit, ProcessUnit | ThoughtUnit>);
  }
  // A block's id is fixed by its START, so it never changes while the block grows: a
  // block that starts with a thought is `th-<ordinal>` (a thinking-only block keeps
  // its id — and its element — when its first tool call lands; a remount there would
  // fold the thought on the spot), else its first PROCESS member's (`seg-<n>`; a note
  // can come to stand in front of a process mid-stream), else its first text's
  // (`txt-<textOrdinal>`). Assigned before the lead merge below, so a block that
  // absorbs the thoughts above the restatement keeps the id it streamed with.
  for (const g of out) {
    if (g.kind !== 'group') continue;
    const own = g.members.filter((m) => !opts.joined?.has(m));
    const ms = own.length ? own : g.members;
    const first = ms[0];
    const p = ms.find((m): m is ProcessUnit => m.kind === 'process');
    const text = ms.find((m): m is TextUnit => m.kind === 'note' || m.kind === 'landmark');
    const key = first.kind === 'thought' ? `th-${first.ordinal}` : p ? p.id : `txt-${(text as TextUnit).textOrdinal}`;
    g.id = `grp-${key}`;
  }
  // Thoughts the model had before writing its restatement belong with the work they
  // led to, not in a block of their own above it. Only once the turn
  // is over (`mergeLead`): while it streams, the restatement is the answer-so-far until
  // a tool call arrives, so moving the thought then would move it under the reader.
  for (let i = 0; opts.mergeLead !== false && i + 2 < out.length; i += 1) {
    const g = out[i];
    const next = out[i + 2];
    if (g.kind === 'group' && out[i + 1] === opts.exclude && next.kind === 'group'
      && g.members.every((m) => m.kind === 'thought')) {
      next.members.unshift(...g.members);
      out.splice(i, 1);
    }
  }
  const last = out[out.length - 1];
  const beforeLast = out[out.length - 2];
  if (isStreaming && last && last.kind === 'group') last.live = true;
  else if (isStreaming && opts.holdLiveAboveAnswer && last?.kind === 'outcome'
    && beforeLast?.kind === 'group' && beforeLast.hasTools) {
    // The answer-so-far streams below this block: keep the block open (and its one
    // spinner) until the turn ends or the next tool call joins it.
    beforeLast.live = true;
  }
  else if (isStreaming && (!last || last.kind === 'stage')) {
    // Streaming, and nothing has started since a stage row (or nothing at all yet):
    // an empty WORKING card holds the turn's one spinner until the next item lands.
    const id = `lead-${last ? last.index : 0}`;
    const placeholder: ProcessUnit = { kind: 'process', id, steps: [], isLive: true, live: {} };
    out.push({ kind: 'group', id: `grp-${id}`, members: [placeholder], live: true, hasTools: false });
  }
  return out;
}

// ── Stage sections (pipeline turns) ─────────────────────────────────────────
//
// Each stage row owns the work from its START to the next row's start (XG
// 2026-10-19): the backend writes an `in_progress` block when a stage starts and a
// `completed` / `skipped` block for the same run + stage when it ends. The start
// block opens the section; the end block updates that section's row in place (same
// section, same children, same React key — the row folds where it is). A finished
// stage folds its work under its row (design-judgment Surface 6, "process folds, it
// is not deleted").
//
// Older sessions have only end blocks. For them a row still takes the work before it
// (end-based), the previous non-gate row's time is its start, and work after the last
// row stays under that row once the turn is over.
//
// First level of a pipeline turn: the restatement, ONE pipeline block (its lines are
// the stage / gate rows laid onto the run's checklist), RESULT. So notes before the
// first stage go into the first stage — the leading text stays at the top: it is the
// restatement — and so do thoughts before the work, once the turn has settled
// (`keepLeadThoughts` holds them where they streamed until then). Once the turn is
// over the work after the last row stays under it; only the trailing answer (the
// result) stays at the top.
//
// GATE rows (gate0 / gate1 / gate2) hold no work and say only their verdict. The run
// records a gate's START (Gate 1's prompt hand-out) but not its END — the verdict is
// written when the guarded stage completes — so a gate that owned "its start to the
// next start" would swallow the stage it guards. A gate's own sub-agent is part of
// that stage's work. A gate row sits next to the stage it guards: GATE 0 after
// EVALUATE, GATE 1 before BUILD / GOAL CYCLE, GATE 2 before DELIVER.

export type TurnSection =
  | { kind: 'unit'; unit: TurnUnit }
  | {
      kind: 'stage';
      block: PipelineStageContent;
      children: TurnUnit[];
      /** End-based (older sessions): the previous non-gate row's time. */
      prevAt?: string;
      /** When the stage started: its start block's time, else `prevAt`. */
      startedAt?: string;
      /** A started stage that ended: how many children it had at its end block. Work
       *  after that is still its own (start to next start), unless the next row is
       *  an end block with no start, which then takes it. */
      endedAt?: number;
    }
  | {
      kind: 'liveStage';
      stage: LiveStage;
      /** Place in the run's plan ("5 of 8"), when the previous rows say it. */
      position?: { n: number; total: number };
      children: TurnUnit[];
    };

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

/** A stage row's counts: its tool calls, the files they changed, its shell commands. */
export function stageCounts(units: TurnUnit[]): { steps: number; files: number; commands: number } {
  const steps = units.flatMap((u) => (u.kind === 'process'
    ? u.steps.filter((s): s is ToolStep => s.kind === 'tool') : []));
  return {
    steps: steps.length,
    files: changedFiles(steps).length,
    commands: steps.filter((s) => s.block.name === 'Bash').length,
  };
}

type StageSection = Extract<TurnSection, { kind: 'stage' }>;

/** Where a gate row goes: next to the stage it guards (see the header above). */
function placeGates(out: TurnSection[]): TurnSection[] {
  const gates = out.filter((s): s is StageSection => s.kind === 'stage' && isGateStage(s.block.stage));
  if (!gates.length) return out;
  const rest = out.filter((s) => !(s.kind === 'stage' && isGateStage(s.block.stage)));
  for (const g of gates) {
    const natural = out.indexOf(g);
    // The stage it guards: the nearest one at or before the gate's own position.
    const guards = g.block.stage === 'gate0' ? ['evaluate'] : g.block.stage === 'gate2' ? ['deliver'] : ['build', 'goal_cycle'];
    let target = -1;
    for (let k = rest.length - 1; k >= 0; k -= 1) {
      const s = rest[k];
      if (s.kind === 'stage' && guards.includes(s.block.stage) && s.block.runId === g.block.runId
        && out.indexOf(s) < natural) { target = k; break; }
    }
    if (target < 0) {
      // Not found: keep it where it arrived (after whatever came before it).
      const before = out.slice(0, natural).filter((s) => rest.includes(s)).length;
      rest.splice(before, 0, g);
    } else {
      rest.splice(g.block.stage === 'gate0' ? target + 1 : target, 0, g);
    }
  }
  return rest;
}

export function sectionUnits(
  units: TurnUnit[], isStreaming: boolean, liveStage?: LiveStage,
  opts: { keepLeadText?: boolean; keepLeadThoughts?: boolean } = {},
): TurnSection[] {
  if (!units.some((u) => u.kind === 'stage') && !(isStreaming && liveStage)) {
    return units.map((unit) => ({ kind: 'unit', unit }));
  }
  const keepLeadText = opts.keepLeadText ?? true;
  const out: TurnSection[] = [];
  let buffer: TurnUnit[] = [];
  let i = 0;
  // Before any work: the restatement stays at the top; thoughts go to the first stage
  // — except while the turn streams and settles (`keepLeadThoughts`): they stay where
  // they streamed, above the restatement, and join the work when it folds.
  while (i < units.length && units[i].kind !== 'process' && units[i].kind !== 'stage') {
    const u = units[i];
    if (u.kind === 'thought' ? !opts.keepLeadThoughts : !keepLeadText) buffer.push(u);
    else out.push({ kind: 'unit', unit: u });
    i += 1;
  }
  let current: StageSection | undefined; // a section opened by a start block
  let last: StageSection | undefined; // the latest non-gate section
  let lastWithPosition: PipelineStageContent | undefined;
  const same = (a: PipelineStageContent, b: PipelineStageContent) => a.stage === b.stage && a.runId === b.runId;
  for (; i < units.length; i += 1) {
    const u = units[i];
    if (u.kind !== 'stage') {
      if (current) current.children.push(u);
      else buffer.push(u);
      continue;
    }
    const block = u.block;
    if (isGateStage(block.stage)) {
      out.push({ kind: 'stage', block, children: [] });
      continue;
    }
    if (block.position !== null) lastWithPosition = block;
    if (block.status === 'in_progress') {
      if (current && same(current.block, block) && current.block.status === 'in_progress') continue; // repeated start
      // A new start: the work before it (before the first stage) is this stage's.
      const sec: StageSection = { kind: 'stage', block, children: buffer, startedAt: block.at };
      buffer = [];
      out.push(sec);
      current = sec;
      last = sec;
      continue;
    }
    const open = [...out].reverse().find((s): s is StageSection =>
      s.kind === 'stage' && same(s.block, block) && s.block.status === 'in_progress');
    if (open) {
      // Its end: the same row, updated in place; work so far stays where it is.
      open.block = block;
      if (buffer.length) { open.children.push(...buffer); buffer = []; }
      open.endedAt = open.children.length;
      continue;
    }
    // End-based (no start block): the row takes the work before it — including the
    // work after a started stage's end, which that stage was only holding.
    if (current && current.endedAt !== undefined && block.status !== 'skipped') {
      buffer = [...current.children.splice(current.endedAt), ...buffer];
    }
    const sec: StageSection = { kind: 'stage', block, children: buffer, prevAt: last?.block.at, startedAt: last?.block.at };
    buffer = [];
    out.push(sec);
    last = sec;
    current = undefined;
  }
  // The trailing answer is the result (the answer-so-far while streaming): it stays
  // at the top, never inside a stage — a finished stage folds, and would hide it.
  let trailing: TurnUnit[] = [];
  // (A stage still running and streaming keeps its answer-so-far: it is open and live,
  // and lifting it would move every between-calls note in and out of the stage.)
  if (current && !buffer.length && !(isStreaming && current.block.status === 'in_progress')) {
    let tail = current.children.length;
    while (tail > 0 && current.children[tail - 1].kind === 'outcome') tail -= 1;
    trailing = current.children.splice(tail);
  }
  if (buffer.length) {
    if (isStreaming && liveStage && !current) {
      let tail = buffer.length;
      while (tail > 0 && buffer[tail - 1].kind === 'outcome') tail -= 1;
      trailing = buffer.splice(tail);
      const position = !isGateStage(liveStage.stage) && lastWithPosition && lastWithPosition.position !== null
        ? { n: lastWithPosition.position + 1, total: lastWithPosition.total }
        : undefined;
      out.push({ kind: 'liveStage', stage: liveStage, position, children: buffer });
      buffer = [];
    } else if (!isStreaming) {
      // The turn is over: work after the last row stays under it (a skipped row did
      // no work: the latest row that ran); the trailing answer stays at the top.
      const holder = [...out].reverse().find((sec): sec is StageSection => sec.kind === 'stage'
        && !isGateStage(sec.block.stage) && sec.block.status !== 'skipped');
      let tail = buffer.length;
      while (tail > 0 && buffer[tail - 1].kind === 'outcome') tail -= 1;
      if (holder) {
        holder.children.push(...buffer.slice(0, tail));
        buffer = buffer.slice(tail);
      }
    }
    out.push(...buffer.map((unit): TurnSection => ({ kind: 'unit', unit })));
  }
  out.push(...trailing.map((unit): TurnSection => ({ kind: 'unit', unit })));
  return placeGates(out);
}

// ── The run's checklist (chat display v8, XG 2026-10-19) ─────────────────────
//
// A pipeline turn is ONE block whose header is the run's plan as a checklist: every
// stage and gate the profile runs (`pipeline_run.checklist`, from the backend — the
// frontend holds no gate or profile table). Each line is present from the first frame
// and changes only its status (pending → running → done), so no line is ever
// inserted above one the reader has seen. A stage that runs again (a goal loop, a
// Gate BLOCK back to PLAN) is the same line with one more round. A resumed turn's
// stages before `current` ran in an earlier turn: done, with no work here. A gate
// row the plan did not list sits inside the line of the stage it guards; with no
// such line, it (like any unplanned stage) is appended at the end.
// Two known limits, by design: when the run's PLAN changes mid-run (a profile upgrade
// the CLI allows), the backend re-sends the checklist and the new lines appear where
// the new plan puts them — the plan really changed, so the list says so. And lines
// match sections by stage name, so a second run started in the same turn lays its
// stages onto the first run's lines (as another round).

type RowSection = Exclude<TurnSection, { kind: 'unit' }>;

export interface ChecklistLine {
  stage: string;
  gate: boolean;
  status: 'done' | 'running' | 'pending' | 'skipped';
  /** This line's sections, oldest first: one per round. */
  rounds: RowSection[];
  /** Gate rows the plan did not list, shown inside this (host) line. */
  extraGates: StageSection[];
  /** Done in an earlier turn of the run (a resumed turn): no work here. */
  earlier: boolean;
}

const GATE_HOSTS: Record<string, string[]> = { gate0: ['evaluate'], gate1: ['build', 'goal_cycle'], gate2: ['deliver'] };
const sectionStage = (s: RowSection): string => (s.kind === 'stage' ? s.block.stage : s.stage.stage);

export function pipelineChecklist(sections: TurnSection[], run?: PipelineRunContent): ChecklistLine[] {
  // An older turn's run block has no checklist: its stage list is the plan.
  const plan = run?.checklist?.length ? run.checklist : (run?.stages ?? []).map((stage) => ({ stage, gate: false }));
  const lines: ChecklistLine[] = plan.map((c) => ({
    stage: c.stage, gate: !!c.gate, status: 'pending', rounds: [], extraGates: [], earlier: false,
  }));
  const find = (stage: string) => lines.find((l) => l.stage === stage);
  for (const sec of sections) {
    if (sec.kind === 'unit') continue;
    const name = sectionStage(sec);
    const line = find(name);
    if (line) { line.rounds.push(sec); continue; }
    const gate = isGateStage(name);
    const host = gate && plan.length ? lines.find((l) => (GATE_HOSTS[name] ?? []).includes(l.stage)) : undefined;
    if (host && sec.kind === 'stage') { host.extraGates.push(sec); continue; }
    lines.push({ stage: name, gate, status: 'pending', rounds: [sec], extraGates: [], earlier: false });
  }
  // The lines before the run's current one ran in an earlier turn. With no `current`
  // (the turn's first CLI call fell between two stages), the lines before the first
  // line this turn shows work for did — the plan runs in order.
  const named = run?.current ? lines.findIndex((l) => l.stage === run.current) : -1;
  const currentAt = named >= 0 ? named : lines.findIndex((l) => l.rounds.length > 0);
  lines.forEach((l, k) => {
    const last = l.rounds[l.rounds.length - 1];
    if (!last) {
      if (currentAt >= 0 && k < currentAt) { l.status = 'done'; l.earlier = true; }
      else if (k === currentAt && named >= 0) l.status = 'running';
      return;
    }
    if (last.kind === 'liveStage' || last.block.status === 'in_progress') l.status = 'running';
    else if (last.block.status === 'skipped') l.status = 'skipped';
    else l.status = 'done';
  });
  return lines;
}

// ── Notes ────────────────────────────────────────────────────────────────────

export type NoteUnit = Extract<TurnUnit, { kind: 'note' }>;

/** A note's first line as plain text: markdown markers, emphasis, code ticks and
 *  link targets removed ("## **Fix** the `store`" → "Fix the store"). */
export function noteHeadline(text: string): string {
  const first = text.split('\n').map((l) => l.trim()).find((l) => l.length > 0) ?? '';
  return first
    .replace(/^(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)+/, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/(\*\*|__|`)/g, '')
    .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|$|[.,:;!?])/g, '$1$2')
    .trim();
}

