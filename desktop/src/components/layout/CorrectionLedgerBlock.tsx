/**
 * The correction ledger in the CDF panel (design docs/design/2026-10-30-autonomy-tenets-gaps.md §2):
 * the repeat-correction rate against its bar, and the corrections still waiting for the
 * person to say which kind they are (those the chat picker was not answered for, a Hive
 * delivery sent back, an escalation resolved against Swarm's pick).
 *
 * Key export: `CorrectionLedgerBlock`.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { correctionsService } from '../../services/corrections';
import { CorrectionChoices } from '../../pages/chat/components/CorrectionPicker';
import { tf } from '../../pages/chat/tf';

const KEY = ['corrections', 'ledger'];

export function CorrectionLedgerBlock() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const rate = useQuery({ queryKey: [...KEY, 'rate'], queryFn: () => correctionsService.rate() });
  const waiting = useQuery({ queryKey: [...KEY, 'pending'], queryFn: () => correctionsService.pendingAll() });
  const r = rate.data;
  if (!r && !waiting.data?.length) return null;

  const over = r && r.confirmed > 0 && (r.smallSample ? r.repeat > 2 : (r.rate ?? 0) > r.bar);
  const refresh = () => { void qc.invalidateQueries({ queryKey: KEY }); };

  return (
    <div className="space-y-2" data-testid="cdf-corrections">
      {r && (
        <p className="text-sm">
          <span className={over ? 'font-medium text-[var(--color-status-error)]' : 'font-medium'}>
            {r.confirmed === 0
              ? tf(t, 'ingestion.corrections.none', 'No classified corrections in {{days}} days', { days: r.windowDays })
              : tf(t, 'ingestion.corrections.rate', 'Repeat corrections {{pct}} ({{repeat}}/{{confirmed}}, {{days}} days)',
                { pct: `${Math.round((r.rate ?? 0) * 100)}%`, repeat: r.repeat, confirmed: r.confirmed, days: r.windowDays })}
          </span>
          <span className="text-[var(--color-text-muted)]">
            {' · '}{tf(t, 'ingestion.corrections.bar', 'bar {{bar}}', { bar: `${Math.round(r.bar * 100)}%` })}
            {r.smallSample && r.confirmed > 0 ? ` · ${tf(t, 'ingestion.corrections.small', 'fewer than 10: judged by count')}` : ''}
          </span>
        </p>
      )}
      {(waiting.data?.length ?? 0) > 0 && (
        <ul className="space-y-2">
          {waiting.data!.map((row) => (
            <li key={row.id} className="space-y-0.5">
              <div className="text-sm line-clamp-2">{row.text}</div>
              <CorrectionChoices row={row} onChosen={refresh} showQuestion={false} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
