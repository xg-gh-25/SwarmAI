/**
 * Autonomy Judge — one tag per run; a click opens the five-dimension matrix.
 *
 * The tag shows the worst verdict, not an average: any BLOCK makes the run BLOCK
 * (Run Isolation and Quality are hard constraints), otherwise the number of WARNs,
 * otherwise ✓. N/A and PENDING dimensions do not count. The popover lists every
 * dimension with its role, verdict and real numbers, under one summary sentence.
 * Popover behaviour (placement, Escape, focus, scroll) is shared: ./usePopover.
 */
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { DIMENSIONS, DIMENSION_ROLE, type RunJudge, type Verdict } from './shapes';
import { judgeState, judgeSummary } from './judge';
import { POPOVER_CLASS, usePopover } from './usePopover';

const VERDICT_LABEL: Record<Exclude<Verdict, 'PENDING'>, string> = { PASS: 'PASS', WARN: 'WARN', BLOCK: 'BLOCK', NA: 'N/A' };
const VERDICT_COLOR: Record<Verdict, string> = {
  PASS: 'var(--color-status-success)',
  WARN: 'var(--color-status-warning)',
  BLOCK: 'var(--color-status-error)',
  NA: 'var(--color-text-dim)',
  PENDING: 'var(--color-text-dim)',
};

export function JudgeTag({ judge, title }: { judge: RunJudge; title: string }) {
  const { t } = useTranslation();
  const { open, triggerProps, popoverProps } = usePopover();
  const verdictLabel = (v: Verdict) => (v === 'PENDING' ? t('swarmOut.judge.verdictPending') : VERDICT_LABEL[v]);
  const { state, warns } = judgeState(judge.verdicts);

  const symbol = state === 'BLOCK' ? '✕ BLOCK' : state === 'WARN' ? `! ${warns} WARN` : '✓';
  const symbolColor = state === 'BLOCK' ? VERDICT_COLOR.BLOCK : state === 'WARN' ? VERDICT_COLOR.WARN : VERDICT_COLOR.PASS;

  return (
    <>
      <button
        {...triggerProps}
        aria-label={t('ui.JudgeTag.tagLabel', { symbol })}
        data-testid="judge-tag"
        className="inline-flex items-center gap-1.5 h-[20px] px-2.5 rounded-full border text-[11.5px] whitespace-nowrap bg-[var(--color-card)] text-[var(--color-text-secondary)] border-[var(--color-border)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)]"
      >
        {t('ui.JudgeTag.name')} <b style={{ color: symbolColor }}>{symbol}</b>
        {judge.live && <span className="text-[10.5px] text-[var(--color-text-dim)]">{t('swarmOut.judge.soFar')}</span>}
      </button>
      {open && createPortal(
        <div
          {...popoverProps}
          aria-label={t('ui.JudgeTag.popoverLabel', { title })}
          data-testid="judge-popover"
          className={POPOVER_CLASS}
        >
          <div className="flex items-baseline gap-2">
            <b className="text-[13.5px]">{t('ui.JudgeTag.name')}</b>
            <span className="text-[12px] text-[var(--color-text-muted)] truncate">{title}</span>
          </div>
          <div className="font-mono text-[11.5px] text-[var(--color-text-muted)] mt-0.5">{judge.outcome}</div>
          <p className="my-2.5 text-[13px] leading-relaxed">{judgeSummary(judge)}</p>
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr className="text-[11px] text-[var(--color-text-dim)] text-left">
                <th className="font-medium px-1.5 pb-1">{t('swarmOut.judge.colDimension')}</th>
                <th className="font-medium px-1.5 pb-1">{t('swarmOut.judge.colRole')}</th>
                <th className="font-medium px-1.5 pb-1">{t('swarmOut.judge.colVerdict')}</th>
                <th className="font-medium px-1.5 pb-1">{t('swarmOut.judge.colNumbers')}</th>
              </tr>
            </thead>
            <tbody>
              {judge.verdicts.map((v, i) => (
                <tr key={DIMENSIONS[i]} className="border-t border-[var(--color-border)] align-top">
                  <td className="px-1.5 py-1.5 font-medium whitespace-nowrap">{DIMENSIONS[i]}</td>
                  <td className="px-1.5 py-1.5 text-[11.5px] text-[var(--color-text-muted)] whitespace-nowrap">{t(`swarmOut.judge.role.${DIMENSION_ROLE[i]}`)}</td>
                  <td className="px-1.5 py-1.5 text-[11.5px] font-bold whitespace-nowrap" style={{ color: VERDICT_COLOR[v] }}>{verdictLabel(v)}</td>
                  <td className="px-1.5 py-1.5 text-[var(--color-text-secondary)]">{judge.numbers[i] || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {/* the verdicts are the Hive's recorded run audit (they come with /runs); the
              numbers behind them only once the run's whole record is read */}
          <div className="mt-2 text-[11.5px] text-[var(--color-text-dim)]" data-testid="judge-source">
            {t('swarmOut.judge.source')}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
