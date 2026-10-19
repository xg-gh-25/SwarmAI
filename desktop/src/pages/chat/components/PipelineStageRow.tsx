/**
 * PipelineStageRow — one line of a pipeline turn's checklist (chat display v8).
 *
 * A pipeline turn is ONE PipelineBlock whose header is the run's checklist: every
 * planned stage and gate, from the backend (`pipeline_run.checklist`, by profile).
 * Each line is a PipelineStageRow. `pipeline_stage` blocks are written by
 * backend/core/pipeline_stage_events.py when the backend sees a stage start or finish
 * in run.json — never by the model; the units from a stage's start to the next start
 * are its work (turnView `sectionUnits`). A line (XG 2026-10-19):
 *   - the label is the stage name in the same tag style and left edge as the
 *     WORKED / WORKING header — no circled number.
 *   - `pending`: a planned stage or gate that has not run — dim, nothing to open.
 *     `earlier`: done before this turn (a resumed run) — a check, nothing to open.
 *   - a finished stage is one line of counts — "✓ BUILD · 12 steps · 3 files changed
 *     · 5 commands  4m 43s ›" — and opens to its work on click. No file names and no
 *     model summary on the row: the counts say how much, the body says what. A
 *     duration shows only from 5s.
 *   - a gate line (GATE 0 / 1 / 2) says only its verdict. It holds no work
 *     (sectionUnits), so it has no counts, no duration and no chevron.
 *   - a stage line opens when the stage STARTS (an `in_progress` block; older
 *     sessions: the legacy live row from the previous row's `next`) and the end block
 *     updates the same line in place — same element, its work stays under it.
 *   - a stage that runs again (a gate sent it back, a goal loop) is the same line,
 *     marked "round N" (`round`); its work is split by round.
 *   - the stage now running stays open. It carries no spinner — the live work below
 *     holds the one spinner.
 *   - colour only for exceptions: a Gate verdict other than PASS (WARN amber, BLOCK
 *     red), an unrecovered failure. A normal finish is neutral.
 *   - a finished stage that wrote a run document (EVALUATE → REQUIREMENTS.md, PLAN →
 *     DESIGN_SPEC.md, DELIVER → REVIEW.md; `block.doc`) ends with the document's name:
 *     a link that opens it in the Canvas, beside — never inside — the row's toggle.
 *
 * Verdicts are model-written text from run.json: rendered as plain text.
 *
 * @exports PipelineStageRow
 */
import type React from 'react';
import { useContext, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PipelineStageContent } from '../../../types';
import { useLinger } from '../../../hooks/useLinger';
import { isGateStage } from '../turnView';
import { tf } from '../tf';
import { GROUP_TAG_CLASS, ROW_ICON_SLOT } from './WorkGroup';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { ReadOnlyContext } from '../readOnlyContext';

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
  /** A planned line that has not run yet (no block): dim, nothing to open. */
  pending?: string;
  /** A line done in an earlier turn of the run (a resumed turn): no work here. */
  earlier?: string;
  /** This stage's round (a stage that ran again): shown from round 2. */
  round?: number;
}

/** A duration under this many seconds is not shown. */
const MIN_DURATION_S = 5;

/** A planned line with no row of its own in this turn: pending, or done earlier. */
function PlanLine({ stage, earlier }: { stage: string; earlier: boolean }) {
  const { t } = useTranslation();
  return (
    <div data-testid="pipeline-stage-pending" className="border-t border-[var(--color-border)] first:border-t-0">
      <div className="w-full flex items-center gap-1.5 py-1.5 text-[12.5px] min-w-0">
        <span aria-hidden="true" className={`material-symbols-outlined text-[14px] leading-none ${ROW_ICON_SLOT} text-[var(--color-text-dim)]`}>
          {earlier ? 'check' : 'radio_button_unchecked'}
        </span>
        <span data-testid="stage-label" className={`${GROUP_TAG_CLASS} ${earlier ? 'text-[var(--color-text-secondary)]' : 'text-[var(--color-text-dim)]'}`}>
          {stageName(stage)}
        </span>
        {earlier && <span className="shrink-0 text-[var(--color-text-dim)]">{tf(t, 'chat.turn.stage.earlier', 'done earlier')}</span>}
      </div>
    </div>
  );
}

export function PipelineStageRow(props: PipelineStageRowProps) {
  if (props.pending || props.earlier) return <PlanLine stage={(props.pending ?? props.earlier)!} earlier={!!props.earlier} />;
  return <StageRow {...props} />;
}

function StageRow({
  block, live, position, running, startedAt, counts, failed, mustOpen, defaultOpen, choice, onChoose, children, round,
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
  const tookRaw = block && !isGate && !isLive && !unfinished ? secondsBetween(startedAt, block.at) : null;
  const took = tookRaw !== null && tookRaw >= MIN_DURATION_S ? tookRaw : null;
  const countText = !isLive && !isGate && !skipped && counts ? [
    counts.steps > 0 && tf(t, 'chat.turn.group.steps', counts.steps === 1 ? '{{count}} step' : '{{count}} steps', { count: counts.steps }),
    counts.files > 0 && tf(t, 'chat.turn.group.files', counts.files === 1 ? '{{count}} file changed' : '{{count}} files changed', { count: counts.files }),
    counts.commands > 0 && tf(t, 'chat.turn.group.commands', counts.commands === 1 ? '{{count}} command' : '{{count}} commands', { count: counts.commands }),
  ].filter(Boolean).join(' · ') : '';

  const Head = hasBody ? 'button' : 'div';
  const doc = block?.status === 'completed' ? block.doc : null;
  return (
    <div
      data-testid={isLive ? 'pipeline-stage-live' : 'pipeline-stage-row'}
      data-open={open ? 'true' : 'false'}
      className="border-t border-[var(--color-border)] first:border-t-0"
    >
      <div className="flex items-center gap-2 min-w-0">
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
        className={`flex-1 flex items-center gap-1.5 py-1.5 text-left text-[12.5px] text-[var(--color-text-muted)] min-w-0 rounded ${hasBody ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50' : ''}`}
      >
        <span aria-hidden="true" className={`material-symbols-outlined text-[14px] leading-none ${ROW_ICON_SLOT} text-[var(--color-text-dim)]`}>
          {isLive ? 'radio_button_checked' : skipped ? 'remove' : unfinished ? 'more_horiz' : 'check'}
        </span>
        <span data-testid="stage-label" className={`${GROUP_TAG_CLASS} ${isLive ? 'text-[var(--color-text)]' : 'text-[var(--color-text-secondary)]'}`}>
          {label}
        </span>
        {round !== undefined && round > 1 && (
          <span data-testid="stage-round-label" className="shrink-0 text-[var(--color-text-secondary)]">
            {tf(t, 'chat.turn.stage.round', 'round {{n}}', { n: round })}
          </span>
        )}
        {isLive ? (
          <span className="shrink-0 text-[var(--color-text-secondary)]">
            {tf(t, 'chat.turn.stage.running', 'in progress')}
            {position && ` · ${tf(t, 'chat.turn.stage.position', 'stage {{position}} of {{total}}', { position: position.n, total: position.total })}`}
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
      {doc && <StageDocLink doc={doc} />}
      </div>
      {open && <div data-testid="stage-body" className="pl-[22px] pb-2">{children}</div>}
    </div>
  );
}

/** The run document a finished stage wrote: its name, opening it in the Canvas. */
function StageDocLink({ doc }: { doc: NonNullable<PipelineStageContent['doc']> }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const cls = 'shrink-0 inline-flex items-center gap-1 font-mono text-[11.5px] text-[var(--color-text-muted)]';
  const body = (
    <>
      <span aria-hidden="true" className="material-symbols-outlined text-[13px] leading-none">description</span>
      {doc.name}
    </>
  );
  if (readOnly) return <span data-testid="stage-doc" className={cls}>{body}</span>;
  return (
    <button
      type="button"
      data-testid="stage-doc"
      title={tf(t, 'chat.turn.stage.openDoc', 'Open {{name}} in Canvas', { name: doc.name })}
      onClick={() => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: doc.absolutePath } }))}
      className={`${cls} hover:text-[var(--color-text)] hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50 rounded`}
    >
      {body}
    </button>
  );
}
