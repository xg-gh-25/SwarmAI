/**
 * PipelineBlock — a pipeline turn's ONE special WORKED block (chat display v8,
 * XG 2026-10-19).
 *
 * A turn with no pipeline reads like every mainstream agent chat: text, one Worked
 * line, the answer. A pipeline turn is the same shape with one special block: its
 * header is the run's plan as a checklist (`pipelineChecklist`, turnView.ts — the
 * plan itself comes from the backend's `pipeline_run.checklist`):
 *   - live: "PIPELINE · BUILD · 5/11 · 9m 12s" over every planned stage and gate,
 *     done ✓ / running ● / pending ○. Lines are present from the first frame and only
 *     change status, so nothing is inserted above what the reader has seen. The
 *     running line is open (its work, the one live WORKING card); a line folds
 *     FOLD_DELAY_MS after it ends (PipelineStageRow).
 *   - folded (FOLD_DELAY_MS after the answer starts, or a history mount):
 *     "PIPELINE · 11/11 · 32m · 7 files changed · Gate 2 10/11 fixed"; it opens to
 *     the checklist on click. A turn that ended without an answer stays open (where
 *     it stopped).
 * A stage that ran again is the same line ("round 2", the body split by round).
 *
 * @exports PipelineBlock, PipelineBlockProps, BLOCK_MIN_DURATION_S
 */
import type React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLinger } from '../../../hooks/useLinger';
import type { ChecklistLine } from '../turnView';
import { tf } from '../tf';
import { GROUP_TAG_CLASS, ROW_ICON_SLOT } from './WorkGroup';

/** A duration under this many seconds is not shown (it carries no information). */
export const BLOCK_MIN_DURATION_S = 5;

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
    : `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;

const stageName = (stage: string) => stage.replace(/_/g, ' ').replace(/^gate(\d)$/i, 'gate $1').toUpperCase();

export interface PipelineBlockProps {
  lines: ChecklistLine[];
  /** The block is live: the turn streams and its answer has not started. */
  live: boolean;
  /** The turn ended without an answer: stay open where it stopped. */
  stoppedHere: boolean;
  /** When the block's first line started / its last line ended (ISO). */
  startedAt?: string;
  endedAt?: string;
  /** Files the run's work changed in this turn. */
  files: number;
  /** The user's open/closed choice; undefined = automatic. */
  choice?: boolean;
  onChoose: (open: boolean) => void;
  /** Something in it needs the user (an unanswered question / permission): open. */
  mustOpen?: boolean;
  /** One element per line, in `lines` order (PipelineStageRow or its pending form). */
  children: React.ReactNode[];
}

export function PipelineBlock({ lines, live, stoppedHere, startedAt, endedAt, files, choice, onChoose, mustOpen, children }: PipelineBlockProps) {
  const { t } = useTranslation();
  const auto = useLinger(live);
  const [hover, setHover] = useState(false);
  const open = !!mustOpen || (choice ?? (auto || stoppedHere));
  const done = lines.filter((l) => l.status === 'done' || l.status === 'skipped').length;
  const running = lines.find((l) => l.status === 'running' && !l.gate);
  const a = startedAt ? Date.parse(startedAt) : NaN;
  const b = live ? Date.now() : endedAt ? Date.parse(endedAt) : NaN;
  const secs = Number.isFinite(a) && Number.isFinite(b) && b >= a ? Math.round((b - a) / 1000) : null;
  const gate2 = lines.find((l) => l.stage === 'gate2' && l.status === 'done');
  const g2Round = gate2?.rounds[gate2.rounds.length - 1];
  const g2Verdict = g2Round?.kind === 'stage' ? g2Round.block.gate?.verdict : undefined;
  const facts = [
    live && running ? stageName(running.stage) : null,
    `${done}/${lines.length}`,
    secs !== null && secs >= BLOCK_MIN_DURATION_S ? formatSeconds(secs) : null,
    !live && files > 0 ? tf(t, 'chat.turn.group.files', files === 1 ? '{{count}} file changed' : '{{count}} files changed', { count: files }) : null,
    !live && g2Verdict ? tf(t, 'chat.turn.pipeline.gate2', 'Gate 2 {{verdict}}', { verdict: g2Verdict }) : null,
  ].filter(Boolean).join(' · ');
  return (
    <div data-testid="pipeline-block" data-live={live ? 'true' : 'false'} data-open={open ? 'true' : 'false'}
      className={live ? 'my-1.5 border-l-2 border-[var(--color-text-muted)] bg-[var(--color-hover)]/40 rounded-r-lg px-2.5 py-1.5' : 'my-0.5'}>
      <button
        type="button"
        data-testid="pipeline-head"
        aria-expanded={open}
        onClick={() => onChoose(!open)}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className="-ml-1 w-full flex items-center gap-1.5 min-w-0 px-1 py-0.5 rounded-md text-left text-[12.5px] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50"
      >
        <span aria-hidden="true"
          className={`material-symbols-outlined text-[15px] shrink-0 transition-transform duration-150 ${ROW_ICON_SLOT} ${hover || open ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}
          style={{ transform: open ? 'rotate(90deg)' : 'none' }}>
          {/* No spinner here: the running line's WORKING card holds the turn's one spinner. */}
          chevron_right
        </span>
        <span className={`${GROUP_TAG_CLASS} ${live ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]'}`}>
          {tf(t, 'chat.turn.pipeline.label', 'Pipeline')}
        </span>
        {facts && <span data-testid="pipeline-facts" className="truncate min-w-0 tabular-nums">· {facts}</span>}
      </button>
      {open && <div data-testid="pipeline-lines" className="mt-0.5">{children}</div>}
    </div>
  );
}
