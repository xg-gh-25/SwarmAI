/**
 * ThoughtBlock — one thinking passage, as narration in the turn's stream.
 *
 * Zero per-item chrome (design-judgment Surface 6, chat display v7.11): a thought
 * carries no label row and no "Show all / Show less" control. Dim italic text says
 * it is thinking; a clamp's ellipsis says there is more; the TEXT itself is the
 * control — click, Enter or Space opens it in place and closes it again.
 * - Clamp: THOUGHT_LINES while the turn streams, COMPACT_LINES once it is over,
 *   INTENT_LINES for the turn's intent. A thought short enough to fit is not a control.
 * - The intent (the turn's first substantive thought, `deriveTurnView`) is the one
 *   thought with a label: "Intent" — the place to check how the model read the ask.
 *   It is the turn's INTENT only when no run requirement or restatement outranks it
 *   (AssistantMessageView); otherwise it is an ordinary thought inside the work.
 * - While it streams, its newest part wraps in place. Outside a work group (the
 *   intent) it carries the turn's one spinner; inside a WORKING card the card owns it.
 * The open/closed choice is lifted (AssistantMessageView keys it by ordinal), so it
 * survives the stream-end reconcile.
 *
 * @exports ThoughtBlock, THOUGHT_LINES, INTENT_LINES, COMPACT_LINES, isLongThought
 */
import type React from 'react';
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

/** Whether a thought is longer than its clamp (decides whether the text is a control). */
export function isLongThought(text: string, lines: number): boolean {
  const rows = text.trim().split('\n').reduce((n, l) => n + Math.max(1, Math.ceil(l.length / CHARS_PER_LINE)), 0);
  return rows > lines;
}

export interface ThoughtBlockProps {
  unit: ThoughtUnit;
  /** The turn is over: a non-intent thought clamps to COMPACT_LINES. */
  compact?: boolean;
  /** Inside a WORKING card: the card holds the spinner. */
  inGroup?: boolean;
  /** The user's open choice; undefined = clamped. */
  expanded?: boolean;
  onToggle: (open: boolean) => void;
}

export function ThoughtBlock({ unit, compact, inGroup, expanded, onToggle }: ThoughtBlockProps) {
  const { t } = useTranslation();
  // Inside a block a thought is never the INTENT — that label belongs to the first
  // level only (another source outranked it; AssistantMessageView decides).
  const isIntent = unit.intent && !inGroup;
  const lines = isIntent ? INTENT_LINES : compact ? COMPACT_LINES : THOUGHT_LINES;
  const long = !unit.live && isLongThought(unit.text, lines);
  const open = !!expanded;
  const onClick = () => {
    // Selecting part of a thought to copy it is not a request to open or close it.
    if (window.getSelection()?.toString()) return;
    onToggle(!open);
  };
  // Through a click, so the transcript's click-capture anchoring holds the text in place.
  const onKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); }
  };
  const spin = unit.live && !inGroup;
  return (
    <div data-testid="thought" data-live={unit.live ? 'true' : 'false'} data-intent={isIntent ? 'true' : 'false'}>
      {(isIntent || spin) && (
        <div className="mb-0.5 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          {spin && <span aria-hidden="true" className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>}
          <span>{isIntent ? tf(t, 'chat.turn.intent', 'Intent') : tf(t, 'chat.turn.thinking', 'Thinking')}</span>
        </div>
      )}
      <div
        data-testid="thought-text"
        {...(long ? {
          role: 'button',
          tabIndex: 0,
          'aria-expanded': open,
          onClick,
          onKeyDown,
        } : {})}
        className={`text-[12.5px] italic leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap break-words ${long ? 'cursor-pointer rounded hover:text-[var(--color-text-muted)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
        style={!unit.live && long && !open
          ? { display: '-webkit-box', WebkitLineClamp: lines, WebkitBoxOrient: 'vertical', overflow: 'hidden' }
          : undefined}
      >
        {unit.live ? thoughtTail(unit.text) : unit.text}
      </div>
    </div>
  );
}
