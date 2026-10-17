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
 * - `process`   — a run of ADJACENT tool calls / thinking. Rendered as one card:
 *                 folded once finished, open while it is the live (last) unit.
 * - `note`      — text the agent wrote BETWEEN tool calls. Rendered in the stream.
 * - `landmark`  — a pipeline landmark / gate verdict line. Stays visible in the
 *                 main stream and splits the process into segments.
 * - `stage`     — a `pipeline_stage` block: a stage the BACKEND saw finish in
 *                 run.json (not model text). Stays visible, splits the process, and
 *                 names the next stage on the live row while the turn streams.
 * - `outcome`   — text AFTER the last tool call: the answer. Rendered in full.
 * - `interrupt` — a block that needs the user (question / permission / escalation).
 *                 Never folded; also splits the process.
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
 * @exports deriveTurnView, stepLabel, extractNextSteps, summarizeProcess,
 *          isLandmarkText, and the TurnView / TurnUnit / ProcessUnit / Step /
 *          ToolStep / ToolStatus / StepLabel / StepVerb / ProcessSummary /
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
  /** An Agent step's live sub-agent status (`subagent_progress`), while streaming. */
  progress?: SubAgentProgress;
}

export interface ThinkingStep {
  kind: 'thinking';
  text: string;
  index: number;
}

export type Step = ToolStep | ThinkingStep;

export type InterruptBlock = AskUserQuestionContent | CmdPermissionContent | EscalationContent;

export interface ProcessUnit {
  kind: 'process';
  /** `seg-<ordinal>` — the segment's position among the message's process
   *  segments. Ordinals survive both appending (streaming) and the stream-end
   *  reconcile, which drops live-only tool_result blocks and would shift any
   *  block-index key. */
  id: string;
  /** ADJACENT tool calls / thinking, in stream order. */
  steps: Step[];
  /** True only for the last unit of a streaming message — the work in progress. */
  isLive: boolean;
  /** What the live row shows: the pending tool (if any), the newest finished
   *  step, and — when the newest step is thinking — that thought's newest part
   *  (`thoughtTail`, multi-line). */
  live?: {
    step?: ToolStep; thinking?: string; recent?: ToolStep;
    /** The pipeline stage now running: the `next` of the latest stage row before
     *  this segment in the same message (absent outside pipeline turns). */
    stage?: { stage: string; ordinal: number | null };
  };
  /** The turn's final tool failed and nothing followed it — shown outside the fold. */
  unrecoveredFailure?: ToolStep;
}

export type TurnUnit =
  | ProcessUnit
  | { kind: 'landmark'; text: string; index: number }
  /** Text the model wrote between tool calls — shown in the stream, in order. */
  | { kind: 'note'; text: string; index: number }
  | { kind: 'stage'; block: PipelineStageContent; index: number }
  | { kind: 'outcome'; text: string; index: number }
  | { kind: 'interrupt'; block: InterruptBlock; index: number };

export interface TurnView {
  units: TurnUnit[];
  /** Action lines lifted from a trailing ```next-steps block of the final outcome. */
  nextSteps: string[];
  /** What Copy copies: the outcome text (no process notes, no next-steps block). */
  copyText: string;
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
    if (isSubAgentBlock(b)) continue;
    if (b.type !== 'tool_result') seen = true;
    if (b.type === 'text' || b.type === 'thinking') { seenNonTool = true; gate = false; }
    if (INTERRUPT_TYPES.has(b.type)) gate = isUnanswered(b as InterruptBlock);
  }

  const units: TurnUnit[] = [];
  let seg: ProcessUnit | null = null;
  let ordinal = 0;
  // Stream order is never changed: a process segment is a run of ADJACENT
  // tool calls / thinking; any text, stage row or interrupt closes it.
  // Non-empty thinking is persisted with the turn, so a thinking-only segment
  // survives the reload reconcile and ordinals stay stable.
  const openSeg = (): ProcessUnit => {
    if (!seg) {
      seg = { kind: 'process', id: `seg-${ordinal}`, steps: [], isLive: false };
      ordinal += 1;
      units.push(seg);
    }
    return seg;
  };

  content.forEach((block, index) => {
    if (block.type === 'tool_result') return; // consumed by its tool_use
    if (isSubAgentBlock(block)) return; // nested under its Agent step below
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
        units.push({ kind: 'landmark', text, index });
      } else {
        // What the model says between tool calls stays in the stream, in order.
        units.push({ kind: 'note', text, index });
      }
      return;
    }
    if (block.type === 'thinking') {
      const text = block.thinking || '';
      if (!text.trim()) return;
      openSeg().steps.push({ kind: 'thinking', text, index });
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
                : index === lastToolIdx
                  ? 'unknown'
                  : 'done';
      const step: ToolStep = { kind: 'tool', block, index, status, result };
      const subs = children.get(block.id);
      if (subs) {
        // A sub-agent step is done when its own result came back; without one it
        // shares its Agent's fate (running while it runs, cut off if it was).
        step.children = subs.map(({ block: sb, index: si }) => {
          const r = results.get(sb.id);
          const s: ToolStatus = r?.isError ? 'error' : r ? 'done'
            : status === 'pending' || status === 'unknown' || status === 'waiting' ? status : 'done';
          return { kind: 'tool', block: sb, index: si, status: s, result: r };
        });
      }
      if (isStreaming && progress?.[block.id]) step.progress = progress[block.id];
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

  if (isStreaming) {
    let last = units[units.length - 1];
    if (!last) {
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
      // The latest thought, but only while it is the newest thing in the segment.
      const newest = last.steps[last.steps.length - 1];
      const thinking = newest?.kind === 'thinking' ? thoughtTail(newest.text) : undefined;
      // The newest step that already finished, so the row always says what was
      // just done — not only a bare spinner between two tool calls.
      const recent = [...last.steps].reverse().find(
        (s): s is ToolStep => s.kind === 'tool' && s.status !== 'pending',
      );
      last.live = { step: pending, thinking, recent };
      const stageAt = units.map((u) => u.kind).lastIndexOf('stage');
      const prevStage = stageAt >= 0 ? (units[stageAt] as Extract<TurnUnit, { kind: 'stage' }>) : undefined;
      // A landmark after the latest stage row (e.g. "Gate 1: BLOCK" → back to plan)
      // means that row's `next` may no longer hold — show no stage rather than a stale one.
      const landmarkAfter = units.slice(stageAt + 1).some((u) => u.kind === 'landmark');
      if (prevStage?.block.next && !landmarkAfter) {
        last.live.stage = { stage: prevStage.block.next, ordinal: prevStage.block.nextOrdinal };
      }
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
  return { units, nextSteps, copyText };
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

/** English [live, done] verb for each step kind — the i18n fallback. */
export const STEP_VERB_FALLBACK: Record<StepVerb, [string, string]> = {
  read: ['Reading', 'Read'], edit: ['Editing', 'Edited'], search: ['Searching', 'Searched'],
  run: ['Running', 'Ran'], list: ['Listing', 'Listed'], fetch: ['Fetching', 'Fetched'],
  websearch: ['Searching web', 'Searched web'], loadTools: ['Loading tools', 'Loaded tools'],
  skill: ['Using skill', 'Used skill'], todo: ['Updating', 'Updated'], agent: ['Running agent', 'Ran agent'],
  mcp: ['Calling', 'Called'], tool: ['Running', 'Ran'],
};

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
  let rest = cmd;
  for (let guard = 0; guard < 8; guard += 1) {
    const next = rest.replace(SETUP_RE, '').replace(WRAPPER_RE, '');
    if (next === rest) break;
    rest = next;
  }
  if (SETUP_ONLY_RE.test(rest)) return { verb: 'run', object: '…' };
  const segments = splitSegments(rest);
  const segment = segments[0] ?? '';
  // Redirections (`2>/dev/null`, `>out`, `2>&1`) are not arguments.
  const tokens = tokenize(segment).filter((tok) => !/^\d?>/.test(tok) && !/^&?>/.test(tok));
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  const args = tokens.slice(1);
  const nonFlag = args.filter((a) => !a.startsWith('-'));

  if (!segments.every(isReadOnlySegment)) {
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

// ── Collapsed-row summary ───────────────────────────────────────────────────

export interface ProcessSummary {
  /** Tool calls in the segment (thinking is not counted as a step). */
  stepCount: number;
  /** Known failures (only knowable while results are in memory). */
  errorCount: number;
  /** Verbs by frequency, ties in first-appearance order. */
  counts: Array<{ verb: StepVerb; count: number }>;
  /** Thinking passages in the segment. */
  thinkingCount: number;
}

export function summarizeProcess(unit: ProcessUnit): ProcessSummary {
  const order: StepVerb[] = [];
  const tally = new Map<StepVerb, number>();
  let errorCount = 0;
  let stepCount = 0;
  let thinkingCount = 0;
  for (const s of unit.steps) {
    if (s.kind === 'thinking') thinkingCount += 1;
    if (s.kind !== 'tool') continue;
    stepCount += 1;
    if (s.status === 'error') errorCount += 1;
    const v = stepLabel(s.block).verb;
    if (!tally.has(v)) order.push(v);
    tally.set(v, (tally.get(v) ?? 0) + 1);
  }
  const counts = order
    .map((verb, i) => ({ verb, count: tally.get(verb) ?? 0, i }))
    .sort((a, b) => b.count - a.count || a.i - b.i)
    .map(({ verb, count }) => ({ verb, count }));
  return { stepCount, errorCount, counts, thinkingCount };
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
      stage: { stage: string; ordinal: number | null };
      /** Place in the run's plan ("5 of 8"), when the previous rows say it. */
      position?: { n: number; total: number };
      children: TurnUnit[];
    };

export function sectionUnits(units: TurnUnit[], isStreaming: boolean): TurnSection[] {
  if (!units.some((u) => u.kind === 'stage')) return units.map((unit) => ({ kind: 'unit', unit }));
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
  const liveUnit = buffer.find((u): u is ProcessUnit => u.kind === 'process' && u.isLive);
  const stage = liveUnit?.live?.stage;
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
