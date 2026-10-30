/**
 * The correction ledger in the CDF panel (design docs/design/2026-10-30-autonomy-tenets-gaps.md §2).
 * It answers two questions: is the repeat-correction rate over its bar, and which
 * corrections still wait for the person to say which kind they are (a chat line not
 * answered, a Hive delivery sent back, an escalation resolved against Swarm's pick).
 * With fewer than 10 classified corrections the bar is a count (more than 2 repeats),
 * and the line says so instead of showing a percentage.
 *
 * Key export: `CorrectionLedgerBlock`.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { correctionsService, useAnswered } from '../../services/corrections';
import { CorrectionChoices } from '../../pages/chat/components/CorrectionPicker';
import { tf } from '../../pages/chat/tf';
import { OverlayError } from './OverlayState';

const KEY = ['corrections', 'ledger'];
const SMALL_SAMPLE_REPEATS = 2;

const SURFACE_FALLBACK: Record<string, string> = {
  chat: 'Chat', swarm_out_reject: 'Hive delivery sent back', escalation: 'Escalation',
};

export function CorrectionLedgerBlock() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const isAnswered = useAnswered();
  const rate = useQuery({ queryKey: [...KEY, 'rate'], queryFn: () => correctionsService.rate() });
  const waiting = useQuery({ queryKey: [...KEY, 'pending'], queryFn: () => correctionsService.pendingAll() });
  const rows = (waiting.data ?? []).filter((row) => !isAnswered(row.id));
  const r = rate.data;
  const refresh = () => { void qc.invalidateQueries({ queryKey: KEY }); };

  if (rate.isError && waiting.isError) {
    return <OverlayError compact message={tf(t, 'ingestion.corrections.failed', 'Corrections could not be read')}
      onRetry={refresh} testid="cdf-corrections-error" />;
  }
  if (!r && rows.length === 0) return null;

  const over = !!r && r.confirmed > 0
    && (r.smallSample ? r.repeat > SMALL_SAMPLE_REPEATS : (r.rate ?? 0) > r.bar);
  let headline = '';
  if (r) {
    if (r.confirmed === 0) {
      headline = tf(t, 'ingestion.corrections.none', 'No classified corrections in {{days}} days', { days: r.windowDays });
    } else if (r.smallSample) {
      headline = tf(t, 'ingestion.corrections.smallCount',
        '{{repeat}} repeats in {{confirmed}} classified corrections ({{days}} days; under 10, more than 2 repeats is over the bar)',
        { repeat: r.repeat, confirmed: r.confirmed, days: r.windowDays });
    } else {
      headline = tf(t, 'ingestion.corrections.rate', 'Repeat corrections {{pct}} ({{repeat}}/{{confirmed}}, {{days}} days) · bar {{bar}}',
        { pct: `${Math.round((r.rate ?? 0) * 100)}%`, repeat: r.repeat, confirmed: r.confirmed, days: r.windowDays,
          bar: `${Math.round(r.bar * 100)}%` });
    }
  }

  return (
    <div className="space-y-2" data-testid="cdf-corrections">
      <div className="text-xs font-medium text-[var(--color-text-muted)]">{tf(t, 'ingestion.corrections.title', 'Corrections')}</div>
      {headline && (
        <p className={over ? 'text-sm font-medium text-[var(--color-status-error)]' : 'text-sm'}>{headline}</p>
      )}
      {rows.length > 0 && (
        <>
          <div className="text-xs text-[var(--color-text-muted)]">
            {tf(t, 'ingestion.corrections.waiting', '{{n}} waiting for you to say which kind', { n: rows.length })}
          </div>
          <ul className="space-y-2">
            {rows.map((row) => (
              <li key={row.id} className="space-y-0.5">
                <div className="text-xs text-[var(--color-text-faint)]">
                  {tf(t, `ingestion.corrections.surface.${row.surface}`, SURFACE_FALLBACK[row.surface] ?? row.surface)}
                  {row.at ? ` · ${row.at.slice(0, 10)}` : ''}
                </div>
                <div className="text-sm line-clamp-2">{row.text}</div>
                <CorrectionChoices row={row} onChosen={refresh} showQuestion={false} />
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
