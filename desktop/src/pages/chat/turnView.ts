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
 * - `thought`   — a thinking passage, in its place in the stream. The turn's
 *                 `intent` ("Intent") is its first substantive thought within
 *                 the first two tool calls, else its first thought before any.
 * - `note`      — text the agent wrote BETWEEN tool calls; a line of its WORKED block.
 * - `landmark`  — a pipeline landmark / gate verdict line. Splits the process into
 *                 segments; inside a WORKED block it is one of its lines. `blocks`
 *                 marks a gate BLOCK verdict (isGateBlock).
 * - `stage`     — a `pipeline_stage` block: a stage the BACKEND saw finish in
 *                 run.json (not model text). Stays visible, splits the process, and
 *                 names the next stage on the live row while the turn streams.
 * - `outcome`   — text AFTER the last tool call: the answer. Rendered in full.
 * - `interrupt` — a block that needs the user (question / permission / escalation).
 *                 Never folded; also splits the process.
 *
 * A second, pure pass — `groupWork` — gathers everything between two first-level
 * items (thoughts, notes, landmarks, tool calls) into one WORKING / WORKED block
 * (chat display v7.12). `toolCall` is a row's real tool name and command.
 *
 * Not units: the backend's display blocks (`pipeline_run`, `pipeline_attention`,
 * `turn_deliverables`) are lifted into `TurnView.run / attention / deliverables`.
 * They have a fixed place in the turn (alignment / before the result / after it),
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
 * @exports deriveTurnView, groupWork, toolCall, stepLabel,
 *          extractNextSteps, isLandmarkText, sectionUnits, isGateBlock,
 *          and the TurnView / TurnUnit / ProcessUnit / Step / ToolStep /
 *          ToolStatus / StepLabel / StepVerb / ToolCallView / WorkGroup / WorkItem /
 *          NextStepsResult types
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
  /** `blocks`: it states a Gate BLOCK (the run goes back a stage). */
  | { kind: 'landmark'; text: string; index: number; blocks?: boolean }
  /** Text the model wrote between tool calls — shown in the stream, in order. */
  | { kind: 'note'; text: string; index: number }
  | { kind: 'stage'; block: PipelineStageContent; index: number }
  /** A thinking passage. `ordinal` = its place among the turn's thoughts (a stable
   *  key); `live` = it is still streaming; `intent` = the first thought before any
   *  tool call (the model's reading of the ask). */
  | ThoughtUnit
  | { kind: 'outcome'; text: string; index: number }
  | { kind: 'interrupt'; block: InterruptBlock; index: number };

export interface ThoughtUnit {
  kind: 'thought';
  text: string;
  index: number;
  ordinal: number;
  live: boolean;
  intent: boolean;
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
  /** The turn's products (`turn_deliverables`), by absolute path, first-seen order. */
  deliverables: TurnDeliverable[];
  /** Action lines lifted from a trailing ```next-steps block of the final outcome. */
  nextSteps: string[];
  /** What Copy copies: the outcome text (no process notes, no next-steps block). */
  copyText: string;
  /** The content starts with a tool RESULT: the tail of a compacted / resumed turn.
   *  Nothing in it is how the ask was read, so it has no intent. */
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
const DISPLAY_TYPES = new Set(['pipeline_run', 'pipeline_attention', 'turn_deliverables']);

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
  let thoughts = 0;
  let attention: PipelineAttentionContent | undefined;
  const deliverables: TurnDeliverable[] = [];
  content.forEach((block, index) => {
    if (block.type === 'tool_result') return; // consumed by its tool_use
    if (isSubAgentBlock(block)) return; // nested under its Agent step below
    if (block.type === 'pipeline_run') { run ??= block; return; }
    if (block.type === 'pipeline_attention') { attention = block; return; }
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
      const text = block.text || '';
      if (!text.trim()) return;
      seg = null;
      if (index > lastToolIdx) {
        units.push({ kind: 'outcome', text, index });
      } else if (isLandmarkBlock(block, text)) {
        const unit: TurnUnit = { kind: 'landmark', text, index };
        if (isGateBlock(text)) unit.blocks = true;
        units.push(unit);
      } else {
        // What the model says between tool calls stays in the stream, in order.
        units.push({ kind: 'note', text, index });
      }
      return;
    }
    if (block.type === 'thinking') {
      const text = block.thinking || '';
      if (!text.trim()) return;
      seg = null;
      units.push({ kind: 'thought', text, index, ordinal: thoughts, live: false, intent: false });
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

  // The intent thought ("Intent"): the first thought of at least INTENT_MIN_CHARS within the
  // first INTENT_TOOL_WINDOW tool calls — a turn often opens with "let me look at the
  // image first" and only reads the ask after that first look. Else the first thought
  // before any tool call. This loop is the single source; groupWork only reads it.
  {
    let tools = 0;
    let first: ThoughtUnit | undefined;
    let long: ThoughtUnit | undefined;
    // The thought still streaming (the turn's last unit) — `live` is marked below.
    const tail = units[units.length - 1];
    const streamingThought = isStreaming && tail?.kind === 'thought' ? tail : undefined;
    for (const u of units) {
      if (u.kind === 'process') {
        tools += u.steps.length;
        if (tools >= INTENT_TOOL_WINDOW + 1) break;
        continue;
      }
      if (u.kind !== 'thought') continue;
      if (tools === 0 && !first) first = u;
      // Only a FINISHED thought can win on length: one still streaming would take the
      // label mid-sentence and move the layout under the reader as it grows.
      if (u !== streamingThought && u.text.trim().length >= INTENT_MIN_CHARS) { long = u; break; }
    }
    const intent = midWork ? undefined : long ?? first;
    if (intent) intent.intent = true;
  }

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
    if (prevStage?.block.next && !landmarkAfter) {
      liveStage = { stage: prevStage.block.next, ordinal: prevStage.block.nextOrdinal };
    } else if (!prevStage && run?.stages?.length && !landmarkAfter) {
      // Before the first stage row: the plan's first stage is running, so its work
      // sits under that stage from the start and folds in place when the row lands.
      liveStage = { stage: run.stages[0], ordinal: null };
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
  return { units, nextSteps, copyText, run, attention, deliverables, liveStage, midWork };
}

/** Characters of a still-streaming thought shown on the folded live row. */
export const THOUGHT_TAIL_CHARS = 360;

/** The newest part of a (possibly still-streaming) thought, line breaks kept,
 *  so the folded live row WRAPS and grows instead of one line whose text is
 *  replaced each time a new line starts (XG 2026-10-18: "该换行就换行"). Cut at
 *  a line or word boundary, marked with a leading ellipsis. */
export function thoughtTail(text: string, max: number = THOUGHT_TAIL_CHARS): string | undefined {
  const t = text.replace(/\n{3,}/g, '\n\n').trim();
  if (!t) return undefined;
  if (t.length <= max) return t;
  const cut = t.slice(-max);
  const nl = cut.indexOf('\n');
  const sp = cut.search(/\s/);
  const at = nl >= 0 && nl < max / 2 ? nl + 1 : sp >= 0 && sp < 40 ? sp + 1 : 0;
  return `…${cut.slice(at).trimStart()}`;
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

function isReadOnlySegment(segment: string): boolean {
  if (/`|\$\(/.test(segment)) return false; // command substitution runs anything
  const tokens = tokenize(segment);
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  if (!READ_ONLY_HEADS.has(head)) return false;
  for (let i = 1; i < tokens.length; i += 1) {
    // `>file` / `2>> log` write; `2>/dev/null` and `2>&1` do not.
    const redirect = /^(?:\d|&)?>>?(.*)$/.exec(tokens[i]);
    if (redirect && !redirect[1].startsWith('&')) {
      const target = redirect[1] || tokens[i + 1] || '';
      if (target !== '/dev/null') return false;
    }
  }
  if (head === 'sed' && tokens.some((t) => /^-[a-zA-Z]*i/.test(t) || t.startsWith('--in-place'))) return false;
  if (head === 'sort' && tokens.some((t) => t === '-o' || t.startsWith('--output'))) return false;
  if (head === 'find' && tokens.some((t) => FIND_WRITES.has(t))) return false;
  return true;
}

function tokenize(segment: string): string[] {
  const out: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(segment))) out.push(m[1] ?? m[2] ?? m[3]);
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

function labelBash(cmd: string): Pick<StepLabel, 'verb' | 'object' | 'path'> {
  const rest = stripBashSetup(cmd, true);
  if (SETUP_ONLY_RE.test(rest)) return { verb: 'run', object: '…' };
  const segments = splitSegments(rest);
  const segment = segments[0] ?? '';
  // Redirections (`2>/dev/null`, `>out`, `2>&1`) are not arguments.
  const tokens = tokenize(segment).filter((tok) => !/^\d?>/.test(tok) && !/^&?>/.test(tok));
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  const args = tokens.slice(1);
  const nonFlag = args.filter((a) => !a.startsWith('-'));

  if (!segments.every(isReadOnlySegment)) {
    // A write-only command (`cat > a.py <<EOF`, `tee a.md`) changed that file: count it.
    const write = /^(?:cat|tee|printf|echo)$/.test(head) ? writeTarget(segment) : undefined;
    if (write) {
      const full = resolveBashPath(write, leadingCd(cmd));
      return full ? { verb: 'edit', object: basename(write), path: full } : { verb: 'edit', object: basename(write) };
    }
    // Something in it may write: never dress it up as a read. Scripts and known
    // tools keep their short name; anything else shows the command itself.
    if (['python', 'python3', 'node', 'bash', 'sh', 'perl', 'ruby', 'deno'].includes(head)) {
      const script = nonFlag.find((a) => /\.[a-z]{1,4}$/i.test(a));
      if (script) return { verb: 'run', object: basename(script) };
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
  if (['ls', 'tree', 'find'].includes(head)) {
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

// ── Work blocks (chat display v7.12) ───────────────────────────────────────
//
// A turn has two levels. FIRST level: the intent, WORKING / WORKED blocks, pipeline
// stage rows, interrupts and the result. SECOND level, inside a block (or an opened
// stage), in stream order: thoughts, notes the model wrote between tool calls,
// landmarks and tool calls. So a block is EVERYTHING between two first-level items —
// a thought or a note never makes a first-level row of its own. Only the last block of
// a streaming turn is live (the WORKING card); a finished one folds to WORKED.

/** Characters a thought needs to count as the turn's intent rather than an opener. */
export const INTENT_MIN_CHARS = 160;
/** How many tool calls may come before the intent thought. */
export const INTENT_TOOL_WINDOW = 2;

export interface WorkGroup {
  kind: 'group';
  /** `grp-<first process id>` (else `grp-th-<first thought ordinal>`): survives the
   *  stream-end reconcile and the intent moving to a later thought. */
  id: string;
  /** Its second-level items, in stream order. */
  members: WorkMember[];
  /** The last block of a streaming turn. */
  live: boolean;
  /** Has at least one tool call. */
  hasTools: boolean;
}

export type LandmarkUnit = Extract<TurnUnit, { kind: 'landmark' }>;
export type WorkMember = ThoughtUnit | ProcessUnit | NoteUnit | LandmarkUnit;

export type WorkItem = WorkGroup | Exclude<TurnUnit, ProcessUnit>;

export interface GroupWorkOptions {
  /** A note that stays first level: the turn's intent restatement. */
  exclude?: TurnUnit;
  /** The intent thought stays first level (false: another source is the intent, so
   *  that thought is an ordinary member). */
  keepIntent?: boolean;
  /** Merge thoughts-only blocks right before `exclude` into the next block (default
   *  true). False while the turn streams. */
  mergeLead?: boolean;
}

export function groupWork(units: TurnUnit[], isStreaming: boolean, opts: GroupWorkOptions = {}): WorkItem[] {
  const keepIntent = opts.keepIntent ?? true;
  const out: WorkItem[] = [];
  let cur: WorkGroup | null = null;
  const member = (u: TurnUnit): u is WorkMember => {
    if (u === opts.exclude) return false;
    if (u.kind === 'thought') return !(u.intent && keepIntent);
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
    out.push(u as Exclude<TurnUnit, ProcessUnit>);
  }
  // Thoughts the model had before writing its restatement (the INTENT) belong with the
  // work they led to, not in a block of their own above the intent. Only once the turn
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
  // A block's id is its first PROCESS member's (stable `seg-<n>`), else its first
  // item's: it survives the stream-end reconcile and a later block joining it.
  for (const g of out) {
    if (g.kind !== 'group') continue;
    const p = g.members.find((m): m is ProcessUnit => m.kind === 'process');
    const first = g.members[0];
    const key = p ? p.id
      : first.kind === 'thought' ? `th-${first.ordinal}`
        : first.kind === 'process' ? first.id : `${first.kind}-${first.index}`;
    g.id = `grp-${key}`;
  }
  const last = out[out.length - 1];
  if (isStreaming && last && last.kind === 'group') last.live = true;
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
// A `stage` row is written when a stage FINISHES, after its work. So the units
// between two stage rows are that stage's work: a finished stage folds them under
// its row (one line per stage — design-judgment Surface 6, "process folds, it is
// not deleted"); the work after the last row, while streaming, is the stage now
// running and stays open. Text before the first work is the alignment and stays
// in the stream; so does everything after the last row once the turn is over.

export type TurnSection =
  | { kind: 'unit'; unit: TurnUnit }
  | { kind: 'stage'; block: PipelineStageContent; children: TurnUnit[]; prevAt?: string }
  | {
      kind: 'liveStage';
      stage: LiveStage;
      /** Place in the run's plan ("5 of 8"), when the previous rows say it. */
      position?: { n: number; total: number };
      children: TurnUnit[];
    };

export function sectionUnits(units: TurnUnit[], isStreaming: boolean, liveStage?: LiveStage): TurnSection[] {
  if (!units.some((u) => u.kind === 'stage') && !(isStreaming && liveStage)) {
    return units.map((unit) => ({ kind: 'unit', unit }));
  }
  const out: TurnSection[] = [];
  let i = 0;
  // Alignment: whatever the model wrote before it started working.
  while (i < units.length && units[i].kind !== 'process' && units[i].kind !== 'stage') {
    out.push({ kind: 'unit', unit: units[i] });
    i += 1;
  }
  let buffer: TurnUnit[] = [];
  let last: PipelineStageContent | undefined;
  let lastWithPosition: PipelineStageContent | undefined;
  for (; i < units.length; i += 1) {
    const u = units[i];
    if (u.kind === 'stage') {
      out.push({ kind: 'stage', block: u.block, children: buffer, prevAt: last?.at });
      buffer = [];
      last = u.block;
      if (u.block.position !== null) lastWithPosition = u.block;
    } else {
      buffer.push(u);
    }
  }
  if (!buffer.length) return out;
  const stage = liveStage;
  if (isStreaming && stage) {
    const position = stage.stage !== 'gate1' && lastWithPosition && lastWithPosition.position !== null
      ? { n: lastWithPosition.position + 1, total: lastWithPosition.total }
      : undefined;
    out.push({ kind: 'liveStage', stage, position, children: buffer });
  } else {
    out.push(...buffer.map((unit): TurnSection => ({ kind: 'unit', unit })));
  }
  return out;
}

/** The first segment of a stage summary ("GO · profile: full" → "GO"). */
export function stageHeadline(summary: string | null | undefined): string {
  return (summary ?? '').split(/\s+·\s+|[(（]/)[0].trim().slice(0, 48);
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

