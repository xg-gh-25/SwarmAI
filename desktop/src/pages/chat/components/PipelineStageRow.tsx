/**
 * PipelineStageRow — one quiet line for a pipeline stage the backend saw finish.
 *
 * Renders a `stage` unit from `deriveTurnView` (pages/chat/turnView.ts), i.e. a
 * `pipeline_stage` block written by backend/core/pipeline_stage_events.py from
 * run.json — never by the model, so it shows up even when the model wrote no
 * landmark text. Layout follows the design-judgment skeleton: the stage
 * ("⑤ BUILD") is the one thing that stands out; its place in the run ("4/8") is
 * the reference it is compared to; the Gate-1 verdict and the stage's own summary
 * are demoted. No border or fill — whitespace separates it from the process rows.
 *
 * `summary` is model-written text from run.json: it is rendered as plain text
 * (React escapes it), never through the markdown renderer.
 *
 * @exports PipelineStageRow, PipelineStageLive
 */
import { useTranslation } from 'react-i18next';
import type { PipelineStageContent } from '../../../types';
import { tf } from '../tf';

/** ① … ⑳ for 1–20; anything else (null / out of range) → ''. */
function circled(n: number | null | undefined): string {
  return typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 20
    ? String.fromCodePoint(0x2460 + n - 1)
    : '';
}

const stageName = (stage: string) => stage.replace(/_/g, ' ').toUpperCase();

function verdictClass(verdict: string): string {
  const v = verdict.toUpperCase();
  if (v.startsWith('PASS')) return 'text-status-online';
  if (v.startsWith('WARN')) return 'text-amber-400';
  if (v.startsWith('BLOCK')) return 'text-red-400';
  return 'text-[var(--color-text-muted)]';
}

export function PipelineStageRow({ block }: { block: PipelineStageContent }) {
  const { t } = useTranslation();
  const skipped = block.status === 'skipped';
  const label = `${circled(block.ordinal)} ${stageName(block.stage)}`.trim();
  return (
    <div
      data-testid="pipeline-stage-row"
      aria-label={tf(t, skipped ? 'chat.turn.stage.ariaSkipped' : 'chat.turn.stage.ariaDone',
        skipped ? 'Pipeline stage {{stage}} skipped' : 'Pipeline stage {{stage}} done', { stage: label })}
      className="-ml-2 flex items-center gap-2 px-2 py-0.5 text-[13px] text-[var(--color-text-muted)] min-w-0"
    >
      <span
        className={`material-symbols-outlined text-[15px] ${skipped ? 'text-[var(--color-text-dim)]' : 'text-status-online'}`}
        aria-hidden="true"
      >
        {skipped ? 'remove' : 'check_circle'}
      </span>
      <span className="font-medium text-[var(--color-text-secondary)] tabular-nums shrink-0">{label}</span>
      {block.position !== null && block.total > 0 && (
        // Position in this run's stage list — worded as such, so it never reads as a
        // second number for the circled one (⑤ counts the gate slots, 4/8 does not).
        <span className="tabular-nums shrink-0">
          · {tf(t, 'chat.turn.stage.position', 'stage {{position}} of {{total}}', { position: block.position, total: block.total })}
        </span>
      )}
      {skipped && <span className="shrink-0">· {tf(t, 'chat.turn.stage.skipped', 'skipped')}</span>}
      {block.gate && (
        <span className="shrink-0">
          · ★ {block.gate.name} <span className={verdictClass(block.gate.verdict)}>{block.gate.verdict}</span>
        </span>
      )}
      {block.summary && (
        <span className="truncate min-w-0 text-[var(--color-text-dim)]" title={block.summary}>
          · {block.summary}
        </span>
      )}
    </div>
  );
}

/** The stage now running — shown above the live process row while a pipeline
 *  turn streams (turnView's `live.stage`). Same quiet line as a finished stage,
 *  with a spinner instead of a check. */
export function PipelineStageLive({ stage }: { stage: { stage: string; ordinal: number | null } }) {
  const { t } = useTranslation();
  const label = `${circled(stage.ordinal)} ${stageName(stage.stage)}`.trim();
  return (
    <div
      data-testid="pipeline-stage-live"
      role="status"
      aria-label={tf(t, 'chat.turn.stage.ariaRunning', 'Pipeline stage {{stage}} in progress', { stage: label })}
      className="-ml-2 flex items-center gap-2 px-2 py-0.5 text-[13px] text-[var(--color-text-muted)] min-w-0"
    >
      <span className="material-symbols-outlined text-[15px] animate-spin" aria-hidden="true">progress_activity</span>
      <span className="font-medium text-[var(--color-text-secondary)] tabular-nums shrink-0">{label}</span>
      <span className="shrink-0">· {tf(t, 'chat.turn.stage.running', 'in progress')}</span>
    </div>
  );
}
