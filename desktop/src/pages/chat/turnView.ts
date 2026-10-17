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
 * - `process`   — a run of work: tool calls, thinking, and the text the agent wrote
 *                 BETWEEN tool calls (`note` steps). Rendered as one folded row.
 * - `landmark`  — a pipeline landmark / gate verdict line. Stays visible in the
 *                 main stream and splits the process into segments.
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
 * result arrives. Failure (`error`) is only knowable while the tool_result is in
 * memory — results are not persisted, so a reloaded turn reports no failures
 * rather than guessing.
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
}

export interface NoteStep {
  kind: 'note';
  text: string;
  index: number;
}

export interface ThinkingStep {
  kind: 'thinking';
  text: string;
  index: number;
}

export type Step = ToolStep | NoteStep | ThinkingStep;

export type InterruptBlock = AskUserQuestionContent | CmdPermissionContent | EscalationContent;

export interface ProcessUnit {
  kind: 'process';
  /** `seg-<ordinal>` — the segment's position among the message's process
   *  segments. Ordinals survive both appending (streaming) and the stream-end
   *  reconcile, which DROPS tool_result / thinking blocks from the content (the
   *  DB never stores them) and would shift any block-index key. */
  id: string;
  steps: Step[];
  /** True only for the last unit of a streaming message — the work in progress. */
  isLive: boolean;
  /** What the live row shows: the pending tool (if any) and the latest note. */
  live?: { step?: ToolStep; note?: string };
  /** The turn's final tool failed and nothing followed it — shown outside the fold. */
  unrecoveredFailure?: ToolStep;
}

export type TurnUnit =
  | ProcessUnit
  | { kind: 'landmark'; text: string; index: number }
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
function isUnanswered(b: InterruptBlock): boolean {
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
// matched ordinary prose ("aggregate 1", "delegate 2") and split the fold.
const LANDMARK_RE = /(^|\n)\s*(## [✦★]|\*\*PHASE [ABC]|🚀 Pipeline|Pipeline (RESUMED|PAUSED)|━━━)|\bGATE [012]\b/;

export function isLandmarkText(text: string): boolean {
  return LANDMARK_RE.test(text);
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

/** Derive the display units for one assistant message. Pure. */
export function deriveTurnView(content: ContentBlock[], isStreaming: boolean): TurnView {
  const results = buildResultMap(content);

  let lastToolIdx = -1;
  for (let i = content.length - 1; i >= 0; i -= 1) {
    if (content[i].type === 'tool_use') { lastToolIdx = i; break; }
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
    if (b.type !== 'tool_result') seen = true;
    if (b.type === 'text' || b.type === 'thinking') { seenNonTool = true; gate = false; }
    if (INTERRUPT_TYPES.has(b.type)) gate = isUnanswered(b as InterruptBlock);
  }

  const units: TurnUnit[] = [];
  let seg: ProcessUnit | null = null;
  let ordinal = 0;
  // Thinking that arrives with no open segment waits here and joins the NEXT
  // segment. Thinking is never persisted, so a segment made only of it would
  // vanish at reload and shift every later segment's ordinal id.
  let pendingThinking: ThinkingStep[] = [];
  const openSeg = (): ProcessUnit => {
    if (!seg) {
      seg = { kind: 'process', id: `seg-${ordinal}`, steps: [], isLive: false };
      ordinal += 1;
      units.push(seg);
    }
    if (pendingThinking.length) {
      seg.steps.push(...pendingThinking);
      pendingThinking = [];
    }
    return seg;
  };

  content.forEach((block, index) => {
    if (block.type === 'tool_result') return; // consumed by its tool_use
    if (INTERRUPT_TYPES.has(block.type)) {
      seg = null;
      units.push({ kind: 'interrupt', block: block as InterruptBlock, index });
      return;
    }
    if (block.type === 'text') {
      const text = block.text || '';
      if (!text.trim()) return;
      if (index > lastToolIdx) {
        // Thinking right before the answer still belongs before it.
        if (pendingThinking.length && !seg) openSeg();
        seg = null;
        units.push({ kind: 'outcome', text, index });
      } else if (isLandmarkBlock(block, text)) {
        seg = null;
        units.push({ kind: 'landmark', text, index });
      } else {
        openSeg().steps.push({ kind: 'note', text, index });
      }
      return;
    }
    if (block.type === 'thinking') {
      const text = block.thinking || '';
      if (!text.trim()) return;
      const step: ThinkingStep = { kind: 'thinking', text, index };
      if (seg) seg.steps.push(step);
      else pendingThinking.push(step);
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
      openSeg().steps.push({ kind: 'tool', block, index, status, result });
    }
  });

  if (pendingThinking.length) openSeg(); // a thinking-only tail (live, or before nothing)

  if (!isStreaming && lastToolIdx >= 0 && !laterBlock[lastToolIdx]) {
    for (const u of units) {
      if (u.kind !== 'process') continue;
      const tail = u.steps.find((st): st is ToolStep => st.kind === 'tool' && st.index === lastToolIdx);
      if (tail && tail.status === 'error') u.unrecoveredFailure = tail;
    }
  }

  if (isStreaming) {
    const last = units[units.length - 1];
    if (!last) {
      units.push({ kind: 'process', id: 'seg-live', steps: [], isLive: true, live: {} });
    } else if (last.kind === 'process') {
      last.isLive = true;
      const pending = [...last.steps].reverse().find(
        (s): s is ToolStep => s.kind === 'tool' && s.status === 'pending',
      );
      const note = [...last.steps].reverse().find((s): s is NoteStep => s.kind === 'note');
      last.live = { step: pending, note: note?.text };
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

/** The command up to the first UNQUOTED `&&` / `||` / `|` / `;` / newline. */
function firstSegment(cmd: string): string {
  let quote: string | null = null;
  for (let i = 0; i < cmd.length; i += 1) {
    const c = cmd[i];
    if (quote) {
      if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'") { quote = c; continue; }
    if (c === '\n' || c === ';' || c === '|' || (c === '&' && cmd[i + 1] === '&')) {
      return cmd.slice(0, i).trim();
    }
  }
  return cmd.trim();
}

function tokenize(segment: string): string[] {
  const out: string[] = [];
  const re = /"([^"]*)"|'([^']*)'|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(segment))) out.push(m[1] ?? m[2] ?? m[3]);
  return out;
}

const looksLikePath = (t: string): boolean => !t.startsWith('-') && /[./]/.test(t) && !/^\d+(,\d+)?p?$/.test(t);

function labelBash(cmd: string): Pick<StepLabel, 'verb' | 'object'> {
  let rest = cmd;
  for (let guard = 0; guard < 8; guard += 1) {
    const next = rest.replace(SETUP_RE, '').replace(WRAPPER_RE, '');
    if (next === rest) break;
    rest = next;
  }
  if (SETUP_ONLY_RE.test(rest)) return { verb: 'run', object: '…' };
  const segment = firstSegment(rest);
  // Redirections (`2>/dev/null`, `>out`, `2>&1`) are not arguments.
  const tokens = tokenize(segment).filter((tok) => !/^\d?>/.test(tok) && !/^&?>/.test(tok));
  const head = (tokens[0] ?? '').replace(/^.*\//, '');
  const args = tokens.slice(1);
  const nonFlag = args.filter((a) => !a.startsWith('-'));

  if (['cat', 'head', 'tail', 'less', 'sed', 'wc'].includes(head)) {
    const file = [...args].reverse().find(looksLikePath);
    if (file) return { verb: 'read', object: basename(file) };
  }
  if (['grep', 'rg', 'ugrep', 'egrep', 'fgrep'].includes(head)) {
    const pattern = nonFlag.find((a) => !a.startsWith('--')) ?? nonFlag[0];
    if (pattern) return { verb: 'search', object: `"${pattern}"` };
  }
  if (['ls', 'tree', 'find'].includes(head)) {
    const dir = nonFlag.find(looksLikePath) ?? nonFlag[0] ?? '.';
    return { verb: 'list', object: basename(dir) };
  }
  if (['python', 'python3', 'node', 'bash', 'sh', 'perl', 'ruby', 'deno'].includes(head)) {
    const script = nonFlag.find((a) => /\.[a-z]{1,4}$/i.test(a));
    if (script) return { verb: 'run', object: basename(script) };
  }
  if (['git', 'npm', 'npx', 'pnpm', 'yarn', 'uv', 'cargo', 'go', 'gh'].includes(head)) {
    return { verb: 'run', object: [head, ...args.slice(0, 2)].join(' ') };
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
    return { ...labelBash(raw.slice('Running: '.length)), raw };
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
  /** Tool calls in the segment (notes and thinking are not counted as steps). */
  stepCount: number;
  /** Known failures (only knowable while results are in memory). */
  errorCount: number;
  /** Verbs by frequency, ties in first-appearance order. */
  counts: Array<{ verb: StepVerb; count: number }>;
}

export function summarizeProcess(unit: ProcessUnit): ProcessSummary {
  const order: StepVerb[] = [];
  const tally = new Map<StepVerb, number>();
  let errorCount = 0;
  let stepCount = 0;
  for (const s of unit.steps) {
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
  return { stepCount, errorCount, counts };
}
