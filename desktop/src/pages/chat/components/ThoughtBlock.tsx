/**
 * ThoughtBlock — one thinking passage, as narration in the turn's stream.
 *
 * A thought is not a tool step: it stays where it happened and never folds away
 * with the work (XG 2026-10-18: "你不能把已经完成的 response or thoughts 给我搞丢").
 * - While it streams: "Thinking" with the turn's one spinner, and its newest part
 *   (wrapping, never one replaced line).
 * - Once finished: dim italic text clamped to THOUGHT_LINES (the turn's intent —
 *   the first thought before any tool call — to INTENT_LINES), with "Show all" /
 *   "Show less" in place: one level, the full text is always one click away.
 *   `compact` (the turn is over) clamps a non-intent thought to COMPACT_LINES.
 * - The intent is labelled "Approach" (思路): it is how the model planned the
 *   work, not how it read the ask — "Understanding" (理解) is only ever the
 *   restatement or the run's requirement.
 * The open/closed choice is lifted (AssistantMessageView keys it by ordinal), so
 * it survives the stream-end reconcile.
 *
 * @exports ThoughtBlock, THOUGHT_LINES, INTENT_LINES, COMPACT_LINES, isLongThought
 */
import { useTranslation } from 'react-i18next';
import type { ThoughtUnit } from '../turnView';
import { thoughtTail } from '../turnView';
import { tf } from '../tf';

export const THOUGHT_LINES = 3;
export const INTENT_LINES = 6;
/** A finished turn's non-intent thought: one line, the rest one click away. */
export const COMPACT_LINES = 1;
/** Characters a clamped line holds at the chat column width, for the overflow test. */
const CHARS_PER_LINE = 90;

/** Whether a thought is longer than its clamp (decides the "Show all" toggle). */
export function isLongThought(text: string, lines: number): boolean {
  const rows = text.trim().split('\n').reduce((n, l) => n + Math.max(1, Math.ceil(l.length / CHARS_PER_LINE)), 0);
  return rows > lines;
}

export interface ThoughtBlockProps {
  unit: ThoughtUnit;
  /** The turn is over: a non-intent thought clamps to COMPACT_LINES. */
  compact?: boolean;
  /** The user's open choice; undefined = clamped. */
  expanded?: boolean;
  onToggle: (open: boolean) => void;
}

export function ThoughtBlock({ unit, compact, expanded, onToggle }: ThoughtBlockProps) {
  const { t } = useTranslation();
  const lines = unit.intent ? INTENT_LINES : compact ? COMPACT_LINES : THOUGHT_LINES;
  const long = !unit.live && isLongThought(unit.text, lines);
  const open = !!expanded;
  const tag = unit.intent
    ? tf(t, 'chat.turn.approach', 'Approach')
    : tf(t, 'chat.turn.thoughtRow', 'Thought');
  return (
    <div data-testid="thought" data-live={unit.live ? 'true' : 'false'} data-intent={unit.intent ? 'true' : 'false'}>
      <div className="mb-0.5 flex items-center gap-1.5 text-[11px] tracking-wide text-[var(--color-text-muted)]">
        {unit.live && (
          <span aria-hidden="true" className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
        )}
        <span>{unit.live ? tf(t, 'chat.turn.thinking', 'Thinking') : tag}</span>
      </div>
      <div
        data-testid="thought-text"
        className="text-[13px] italic leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap break-words"
        style={!unit.live && long && !open
          ? { display: '-webkit-box', WebkitLineClamp: lines, WebkitBoxOrient: 'vertical', overflow: 'hidden' }
          : undefined}
      >
        {unit.live ? thoughtTail(unit.text) : unit.text}
      </div>
      {long && (
        <button
          type="button"
          data-testid="thought-toggle"
          aria-expanded={open}
          onClick={() => onToggle(!open)}
          className="mt-0.5 text-[11.5px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:underline rounded focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          {open ? tf(t, 'chat.turn.thoughtLess', 'Show less') : tf(t, 'chat.turn.thoughtMore', 'Show all')}
        </button>
      )}
    </div>
  );
}
