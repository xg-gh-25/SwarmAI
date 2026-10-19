/**
 * PipelineStageRow — a pipeline stage as ONE row that folds its own work (v7.1).
 *
 * A `pipeline_stage` block is written by backend/core/pipeline_stage_events.py when
 * the backend sees a stage finish in run.json — never by the model. The units
 * between two stage rows are that stage's work (turnView `sectionUnits`). Every
 * stage and every gate that ran has its own row (XG 2026-10-19):
 *   - the label is the stage name in the same tag style and left edge as the
 *     WORKED / WORKING header — no circled number.
 *   - a finished stage is one line of counts — "✓ BUILD · 12 steps · 3 files changed
 *     · 5 commands  4m 43s ›" — and opens to its work on click. No file names and no
 *     model summary on the row: the counts say how much, the body says what.
 *   - a gate row (GATE 0 / 1 / 2) says only its verdict. It holds no work
 *     (sectionUnits), so it has no counts, no duration and no chevron.
 *   - a stage row opens when the stage STARTS (an `in_progress` block; older
 *     sessions: the legacy live row from the previous row's `next`) and the end block
 *     updates the same row in place — same element, its work stays under it.
 *   - the stage now running stays open: "REVIEW · in progress · stage 5 of 8 ·
 *     next TEST → DELIVER" (`upcoming`, from the run's plan in its pipeline_run block).
 *     It carries no spinner — the live work card below holds the one spinner.
 *   - colour only for exceptions: a Gate verdict other than PASS (WARN amber, BLOCK
 *     red), an unrecovered failure. A normal finish is neutral.
 *
 * Verdicts are model-written text from run.json: rendered as plain text.
 *
 * @exports PipelineStageRow
 */
import type React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PipelineStageContent } from '../../../types';
import { useLinger } from '../../../hooks/useLinger';
import { isGateStage } from '../turnView';
import { tf } from '../tf';
import { GROUP_TAG_CLASS, ROW_ICON_SLOT } from './WorkGroup';

/** "gate1" → "GATE 1", "goal_cycle" → "GOAL CYCLE". */
const stageName = (stage: string) => stage.replace(/_/g, ' ').replace(/^gate(\d)$/i, 'gate $1').toUpperCase();

/** Colour only for exceptions: BLOCK red; WARN, a Gate 0 verdict other than a pass
 *  (UNSUPPORTED, WRONG-FRAME …) and a Gate 2 with findings left open ("51/63 fixed")
 *  amber; a pass neutral. */
function verdictClass(verdict: string): string {
  const v = verdict.trim().toUpperCase();
  const fixed = /^(\d+)\/(\d+) FIXED$/.exec(v);
  if (fixed) return Number(fixed[1]) < Number(fixed[2]) ? 'text-amber-400' : 'text-[var(--color-text-muted)]';
  // A recorded history ("BLOCK→revised→PASS") is graded by where it ended: its LAST verdict word.
  const words = v.match(/\b(NO-GO|BLOCK|WARN|PARTIAL|PASS|SUPPORTED|PROCEED|GO|SKIP(?:PED)?|DONE|RESOLVED)\b/g);
  const end = words ? words[words.length - 1] : v;
  if (end === 'BLOCK') return 'text-red-400';
  if (/^(PASS|SUPPORTED|PROCEED|GO|SKIP|SKIPPED|DONE|RESOLVED)$/.test(end)) return 'text-[var(--color-text-muted)]';
  return 'text-amber-400';
}

const formatSeconds = (s: number): string =>
  s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;

function secondsBetween(from?: string, to?: string): number | null {
  const a = from ? Date.parse(from) : NaN;
  const b = to ? Date.parse(to) : NaN;
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? Math.round((b - a) / 1000) : null;
}

export interface PipelineStageRowProps {
  /** A finished stage; absent for the stage now running. */
  block?: PipelineStageContent;
  /** The stage now running (live) — `{stage, ordinal}` from the previous row's `next`. */
  live?: { stage: string; ordinal: number | null };
  position?: { n: number; total: number };
  /** The live stage only: the next planned stage(s), from the run's plan. */
  upcoming?: string[];
  /** A started stage (`in_progress` block) is running only while its turn streams and
   *  it is the turn's last section; otherwise it did not finish in this turn. */
  running?: boolean;
  /** When the stage started (previous row's `at`, or the turn start). */
  startedAt?: string;
  /** The stage's work in numbers (turnView `stageCounts`). */
  counts?: { steps: number; files: number; commands: number };
  /** The stage's work ended on a failure nobody recovered from. */
  failed?: boolean;
  /** Something inside needs the user (an unanswered question) — never folded away. */
  mustOpen?: boolean;
  /** The turn has no result and ended inside this stage: it starts open (where the
   *  turn stopped); the user's choice still wins. */
  defaultOpen?: boolean;
  /** The user's open/closed choice; undefined = automatic. */
  choice?: boolean;
  onChoose: (open: boolean) => void;
  children?: React.ReactNode;
}

export function PipelineStageRow({
  block, live, position, upcoming, running, startedAt, counts, failed, mustOpen, defaultOpen, choice, onChoose, children,
}: PipelineStageRowProps) {
  const { t } = useTranslation();
  // Running: the legacy live row (no block) or a started stage of the streaming turn.
  const isLive = !block || (block.status === 'in_progress' && !!running);
  // Started in this turn, not ended in it (it ends in a later turn, or never).
  const unfinished = !!block && block.status === 'in_progress' && !running;
  // The section is live (a running stage, or one that just ended and still holds the
  // arriving work): open now, and it folds FOLD_DELAY_MS after that ends — never at once.
  const auto = useLinger(isLive || !!running);
  const [hover, setHover] = useState(false);
  const hasBody = !!children && (!Array.isArray(children) || children.length > 0);
  const open = hasBody && (mustOpen || (choice ?? (auto || !!defaultOpen)));
  const name = block ? block.stage : live?.stage ?? '';
  const label = stageName(name);
  const isGate = isGateStage(name);
  const skipped = block?.status === 'skipped';
  const verdict = block?.gate?.verdict;
  const took = block && !isGate && !isLive && !unfinished ? secondsBetween(startedAt, block.at) : null;
  const countText = !isLive && !isGate && !skipped && counts ? [
    counts.steps > 0 && tf(t, 'chat.turn.group.steps', counts.steps === 1 ? '{{count}} step' : '{{count}} steps', { count: counts.steps }),
    counts.files > 0 && tf(t, 'chat.turn.group.files', counts.files === 1 ? '{{count}} file changed' : '{{count}} files changed', { count: counts.files }),
    counts.commands > 0 && tf(t, 'chat.turn.group.commands', counts.commands === 1 ? '{{count}} command' : '{{count}} commands', { count: counts.commands }),
  ].filter(Boolean).join(' · ') : '';

  const Head = hasBody ? 'button' : 'div';
  return (
    <div
      data-testid={isLive ? 'pipeline-stage-live' : 'pipeline-stage-row'}
      data-open={open ? 'true' : 'false'}
      className="border-t border-[var(--color-border)] first:border-t-0"
    >
      <Head
        {...(hasBody ? { type: 'button' as const, onClick: () => onChoose(!open), 'aria-expanded': open } : {})}
        {...(isLive ? { role: 'status' } : {})}
        aria-label={tf(t,
          isLive ? 'chat.turn.stage.ariaRunning' : skipped ? 'chat.turn.stage.ariaSkipped' : unfinished ? 'chat.turn.stage.ariaUnfinished' : 'chat.turn.stage.ariaDone',
          isLive ? 'Pipeline stage {{stage}} in progress' : skipped ? 'Pipeline stage {{stage}} skipped'
            : unfinished ? 'Pipeline stage {{stage}} not finished in this turn' : 'Pipeline stage {{stage}} done',
          { stage: label })}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className={`w-full flex items-center gap-1.5 py-1.5 text-left text-[12.5px] text-[var(--color-text-muted)] min-w-0 rounded ${hasBody ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span aria-hidden="true" className={`material-symbols-outlined text-[14px] leading-none ${ROW_ICON_SLOT} text-[var(--color-text-dim)]`}>
          {isLive ? 'radio_button_checked' : skipped ? 'remove' : unfinished ? 'more_horiz' : 'check'}
        </span>
        <span data-testid="stage-label" className={`${GROUP_TAG_CLASS} ${isLive ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]'}`}>
          {label}
        </span>
        {isLive ? (
          <span className="shrink-0 text-[var(--color-text-secondary)]">
            {tf(t, 'chat.turn.stage.running', 'in progress')}
            {position && ` · ${tf(t, 'chat.turn.stage.position', 'stage {{position}} of {{total}}', { position: position.n, total: position.total })}`}
            {upcoming && upcoming.length > 0 && (
              <span data-testid="stage-upcoming" className="text-[var(--color-text-dim)]">
                {` · ${tf(t, 'chat.turn.stage.next', 'next {{stages}}', { stages: upcoming.map(stageName).join(' → ') })}`}
              </span>
            )}
          </span>
        ) : skipped ? (
          <span className="shrink-0">{tf(t, 'chat.turn.stage.skipped', 'skipped')}</span>
        ) : unfinished ? (
          <>
            <span data-testid="stage-unfinished" className="shrink-0">{tf(t, 'chat.turn.stage.unfinished', 'not finished in this turn')}</span>
            {countText && <span data-testid="stage-counts" className="truncate min-w-0 tabular-nums">· {countText}</span>}
          </>
        ) : (
          <>
            {/* A gate row says only its verdict; a legacy stage that carries a verdict
                names the gate once — never "GATE1 · ★ GATE 1 WARN". */}
            {verdict && (
              <span className={`shrink-0 ${verdictClass(verdict)}`}>
                {isGate ? verdict : `${block?.gate?.name} ${verdict}`}
              </span>
            )}
            {countText && (
              <span data-testid="stage-counts" className="truncate min-w-0 tabular-nums">· {countText}</span>
            )}
          </>
        )}
        {failed && <span className="shrink-0 text-[12px] text-red-400">{tf(t, 'chat.turn.failedOne', 'failed')}</span>}
        {took !== null && (
          <span className="ml-auto pl-2 shrink-0 text-[12px] text-[var(--color-text-dim)] tabular-nums">{formatSeconds(took)}</span>
        )}
        {hasBody && (
          <span
            aria-hidden="true"
            className={`${took === null ? 'ml-auto' : ''} material-symbols-outlined text-[16px] shrink-0 transition-transform duration-150 ${hover ? 'text-[var(--color-text-muted)]' : 'text-[var(--color-text-dim)]'}`}
            style={{ transform: open ? 'rotate(90deg)' : 'none' }}
          >
            chevron_right
          </span>
        )}
      </Head>
      {open && <div data-testid="stage-body" className="pl-[22px] pb-2">{children}</div>}
    </div>
  );
}
