/**
 * PipelineStageRow — a pipeline stage as ONE row that folds its own work (v7.1).
 *
 * A `pipeline_stage` block is written by backend/core/pipeline_stage_events.py when
 * the backend sees a stage finish in run.json — never by the model. The units
 * between two stage rows are that stage's work (turnView `sectionUnits`):
 *   - a finished stage is one line — "✓ ⑤ BUILD  TDD: 6R→GREEN  changed 5 files  4m 43s ›"
 *     — and opens to its work on click. Progress is the stage's place in the plan,
 *     the summary says its effect; step counts live one level down.
 *   - the stage now running stays open: "⑤ BUILD · in progress · stage 5 of 8 ·
 *     next REVIEW → TEST" (`upcoming`, from the run's plan in its pipeline_run block).
 *     It carries no spinner — the live work card below holds the one spinner.
 *   - a finished stage with no model summary says its effect instead: the names of
 *     the files it changed (plain text — the row is a button, so no chips in it).
 *   - colour only for exceptions: a Gate verdict other than PASS (WARN amber, BLOCK
 *     red), an unrecovered failure. A normal finish is neutral.
 *
 * `summary` / verdicts are model-written text from run.json: rendered as plain text.
 *
 * @exports PipelineStageRow
 */
import type React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PipelineStageContent } from '../../../types';
import { useLinger } from '../../../hooks/useLinger';
import { stageHeadline } from '../turnView';
import { tf } from '../tf';

/** ① … ⑳ for 1–20; anything else (null / out of range) → ''. */
function circled(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 20
    ? String.fromCodePoint(0x2460 + n - 1)
    : '';
}

/** "gate1" → "GATE 1", "goal_cycle" → "GOAL CYCLE". */
const stageName = (stage: string) => stage.replace(/_/g, ' ').replace(/^gate(\d)$/i, 'gate $1').toUpperCase();

function verdictClass(verdict: string): string {
  const v = verdict.toUpperCase();
  if (v.startsWith('WARN')) return 'text-amber-400';
  if (v.startsWith('BLOCK')) return 'text-red-400';
  return 'text-[var(--color-text-muted)]'; // PASS and the rest: neutral
}

/** A summary-less stage row names at most this many changed files. */
const NAMED_FILES = 3;
const baseName = (p: string) => p.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || p;

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
  /** When the stage started (previous row's `at`, or the turn start). */
  startedAt?: string;
  /** Files the stage's work changed. */
  changedCount?: number;
  /** Their paths: named on the row when the stage has no summary. */
  changedFiles?: string[];
  /** The stage's work ended on a failure nobody recovered from. */
  failed?: boolean;
  /** Something inside needs the user (an unanswered question) — never folded away. */
  mustOpen?: boolean;
  /** The user's open/closed choice; undefined = automatic. */
  choice?: boolean;
  onChoose: (open: boolean) => void;
  children?: React.ReactNode;
}

export function PipelineStageRow({
  block, live, position, upcoming, startedAt, changedCount = 0, changedFiles = [], failed, mustOpen, choice, onChoose, children,
}: PipelineStageRowProps) {
  const { t } = useTranslation();
  const isLive = !block;
  // Open while running; folds 2s after it finishes (the same component carries over).
  const auto = useLinger(isLive);
  const [hover, setHover] = useState(false);
  const hasBody = !!children && (!Array.isArray(children) || children.length > 0);
  const open = hasBody && (mustOpen || (choice ?? auto));
  const name = block ? block.stage : live?.stage ?? '';
  const ordinal = block ? block.ordinal : live?.ordinal ?? null;
  const label = `${circled(ordinal)} ${stageName(name)}`.trim();
  const skipped = block?.status === 'skipped';
  const verdict = block?.gate?.verdict;
  const summaryHead = stageHeadline(block?.summary);
  const took = block ? secondsBetween(startedAt, block.at) : null;

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
        aria-label={tf(t, isLive ? 'chat.turn.stage.ariaRunning' : skipped ? 'chat.turn.stage.ariaSkipped' : 'chat.turn.stage.ariaDone',
          isLive ? 'Pipeline stage {{stage}} in progress' : skipped ? 'Pipeline stage {{stage}} skipped' : 'Pipeline stage {{stage}} done',
          { stage: label })}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className={`w-full flex items-center gap-2 py-1.5 text-left text-[13px] text-[var(--color-text-muted)] min-w-0 rounded ${hasBody ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span aria-hidden="true" className="material-symbols-outlined text-[14px] leading-none w-[14px] text-[var(--color-text-dim)] shrink-0">
          {isLive ? 'radio_button_checked' : skipped ? 'remove' : 'check'}
        </span>
        <span className={`font-semibold tracking-wide tabular-nums shrink-0 ${isLive ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]'}`}>
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
        ) : (
          <>
            {/* The ④ GATE row says only its verdict; a legacy stage that carries a
                verdict names the gate once — never "GATE1 · ★ GATE 1 WARN". */}
            {verdict && (
              <span className={`shrink-0 ${verdictClass(verdict)}`}>
                {name === 'gate1' ? verdict : `${block?.gate?.name} ${verdict}`}
              </span>
            )}
            {summaryHead && (
              <span className="truncate min-w-0" title={block?.summary ?? undefined}>{summaryHead}</span>
            )}
          </>
        )}
        {!isLive && !skipped && !summaryHead && changedFiles.length > 0 ? (
          <span data-testid="stage-changed-files" className="truncate min-w-0 text-[12px] text-[var(--color-text-dim)]" title={changedFiles.join('\n')}>
            {tf(t, 'chat.turn.stage.changedNames', 'changed {{files}}', {
              files: changedFiles.slice(0, NAMED_FILES).map(baseName).join(', ')
                + (changedFiles.length > NAMED_FILES ? ` +${changedFiles.length - NAMED_FILES}` : ''),
            })}
          </span>
        ) : !isLive && changedCount > 0 && (
          <span className="shrink-0 text-[12px] text-[var(--color-text-dim)]">
            {tf(t, 'chat.turn.stage.changed', 'changed {{count}} files', { count: changedCount })}
          </span>
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
