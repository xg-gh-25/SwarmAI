/**
 * ThoughtBlock — one thinking passage, as narration in the turn's stream.
 *
 * The focus zone (chat display v8, XG 2026-10-19): what is streaming is shown in
 * full, and a thought that has settled (finished, and a later item arrived
 * FOLD_DELAY_MS ago — WorkGroup's useSettled) is one line:
 * - live, or finished but not settled yet: the WHOLE text, no clamp, never replaced by
 *   the next thought
 * - settled (`compact`): one "Thought" line — with how long it streamed, when this
 *   view saw it stream for 5s or more — and no text; the line is the control that
 *   opens the text in place (like a mainstream "Thought for Ns").
 * No thought has a label beyond that (no "Intent"); the opening restatement, when
 * the model wrote one, is plain text above the work. Outside a work group a live
 * thought carries the turn's one spinner; inside a WORKING card the card owns it.
 * The open/closed choice is lifted (AssistantMessageView keys it by ordinal), so it
 * survives the stream-end reconcile.
 *
 * @exports ThoughtBlock, THOUGHT_MIN_SECONDS
 */
import { useTranslation } from 'react-i18next';
import type { ThoughtUnit } from '../turnView';
import { tf } from '../tf';

/** A thought's duration under this many seconds is not shown. */
export const THOUGHT_MIN_SECONDS = 5;

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

export interface ThoughtBlockProps {
  unit: ThoughtUnit;
  /** Settled (or the turn is over): one "Thought" line, the text one click away. */
  compact?: boolean;
  /** Inside a WORKING card: the card holds the spinner. */
  inGroup?: boolean;
  /** The user's open choice; undefined = closed. */
  expanded?: boolean;
  onToggle: (open: boolean) => void;
  /** How long it streamed, when this view saw it stream (WorkGroup measures it). */
  seconds?: number;
}

const TEXT_CLASS = 'text-[12.5px] italic leading-relaxed text-[var(--color-text-dim)] whitespace-pre-wrap break-words';

export function ThoughtBlock({ unit, compact, inGroup, expanded, onToggle, seconds }: ThoughtBlockProps) {
  const { t } = useTranslation();
  const open = !!expanded;
  const spin = unit.live && !inGroup;
  if (compact && !unit.live) {
    return (
      <div data-testid="thought" data-live="false" data-folded={open ? 'false' : 'true'}>
        <button
          type="button"
          data-testid="thought-toggle"
          aria-expanded={open}
          onClick={() => onToggle(!open)}
          className="flex items-center gap-1.5 px-1.5 py-[3px] -ml-1.5 rounded-md text-[12px] italic text-[var(--color-text-dim)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text-muted)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
        >
          <span>{tf(t, 'chat.turn.thought', 'Thought')}</span>
          {seconds !== undefined && seconds >= THOUGHT_MIN_SECONDS && (
            <span data-testid="thought-seconds" className="not-italic tabular-nums">· {formatSeconds(seconds)}</span>
          )}
        </button>
        {open && <div data-testid="thought-text" className={`${TEXT_CLASS} mt-0.5 mb-1`}>{unit.text}</div>}
      </div>
    );
  }
  return (
    <div data-testid="thought" data-live={unit.live ? 'true' : 'false'}>
      {spin && (
        <div className="mb-0.5 flex items-center gap-1.5 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
          <span aria-hidden="true" className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
          <span>{tf(t, 'chat.turn.thinking', 'Thinking')}</span>
        </div>
      )}
      <div data-testid="thought-text" className={TEXT_CLASS}>{unit.text}</div>
    </div>
  );
}
