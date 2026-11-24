/**
 * Swarm's cognition level — under the queue in the first Needs You tab (design
 * docs/design/2026-10-31-cognition-level.md; layout Knowledge/Mockups/
 * 2026-11-24-needs-you-redesign.html). It answers one question: is Swarm needing the
 * person less? One card, read top to bottom:
 *
 * - the share of the last four weeks' conversations the person did not have to correct,
 *   against the four weeks before. Better / worse is the backend's one trend (outside its
 *   95% interval), coloured only then, worded as the prompt words it (levelLine);
 * - one bar per week (the last four in colour, a dashed line before them); a dot under a
 *   week whose rules changed, its labels in the bar's tooltip;
 * - three numbers: this week, this week's corrections that repeat an earlier one (amber
 *   when any does: the target is none), knowledge used within the window;
 * - where mistakes come from (the monthly audit) as one split bar.
 *
 * Data: GET /ingestion/cognition-level (backend core/ingestion/cognition_level.level).
 * Measured outcomes only, never how much is stored.
 *
 * Key export: `CognitionLevel`.
 */
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ingestionService, type CognitionLevelData, type LevelWeek } from '../../services/ingestion';
import { levelLine } from './cognitionLevelText';
import { Big, CARD, Compare, DIM, Kpis, MUTED, ZoneLabel, useGrown } from './needYouParts';

const pct = (v: number) => Math.round(v * 100);
const OUTCOMES = ['事先没有', '有但没送到', '送到仍犯', 'DDD 内容过时', '无法判断'] as const;
const STAGE_KEY: Record<(typeof OUTCOMES)[number], string> = {
  事先没有: 'missing', 有但没送到: 'notDelivered', 送到仍犯: 'usedWrong', 'DDD 内容过时': 'stale', 无法判断: 'unknown',
};
const STAGE_COLOR: Record<string, string> = {
  missing: 'var(--color-status-error)', notDelivered: 'var(--color-status-warning)',
  usedWrong: 'var(--color-zone-cognitive)', stale: 'var(--color-status-running)',
  unknown: 'color-mix(in srgb, var(--color-text-dim) 60%, transparent)',
};
const WEEK_MS = 7 * 864e5;
/** A week's start (``YYYY-MM-DD``) as local midnight, which is what the backend means. */
const localDay = (day: string) => new Date(`${day}T00:00:00`).getTime();

function Weeks({ weeks, marks, recent }: { weeks: LevelWeek[]; marks: CognitionLevelData['marks']; recent: number }) {
  const { t } = useTranslation();
  const grown = useGrown(weeks.map((w) => `${w.week}:${w.notCorrectedRate}`).join('|'));
  const t0 = weeks.length ? localDay(weeks[0].start) : 0;
  const marksOf = weeks.map(() => [] as string[]);
  for (const m of marks) {
    const i = Math.floor((new Date(m.at).getTime() - t0) / WEEK_MS);
    if (i >= 0 && i < weeks.length) {
      marksOf[i].push(m.kind === 'record' ? t('cogLevel.mark.review') : m.kind === 'version' ? t('cogLevel.mark.version', { v: m.label }) : m.label);
    }
  }
  const split = weeks.length - recent;
  return (
    <>
      <div className="relative mt-[18px] flex h-24 items-end gap-2.5 border-b border-[var(--color-border)]" data-testid="level-weeks"
        role="group" aria-label={t('cogLevel.weeksLabel')}>
        {weeks.map((w, i) => {
          const r = w.notCorrectedRate;
          const tip = [t('cogLevel.point', { week: w.start, rate: r == null ? '—' : `${pct(r)}%`, n: w.judged }),
            marksOf[i].length ? t('cogLevel.changed', { what: marksOf[i].join(', ') }) : null].filter(Boolean).join(' · ');
          return (
            <div key={w.week} role="img" aria-label={tip} title={tip} data-testid="level-week"
              className="relative flex h-full flex-1 items-end justify-center">
              <span className="so-grow block w-full max-w-[46px] rounded-t-[3px]"
                style={{ height: `${r == null ? 0 : r * 100}%`, transform: grown ? 'scaleY(1)' : 'scaleY(0)', transitionDelay: `${i * 40}ms`,
                  background: i < split ? 'color-mix(in srgb, var(--color-text-dim) 45%, transparent)'
                    : 'color-mix(in srgb, var(--color-status-success) 70%, transparent)' }} />
              {marksOf[i].length > 0 && <span aria-hidden data-testid="level-mark"
                className="absolute -bottom-[6px] h-[5px] w-[5px] rounded-full" style={{ background: 'var(--color-zone-cognitive)' }} />}
            </div>
          );
        })}
        {split > 0 && <span aria-hidden className="absolute top-0 -bottom-px border-l border-dashed"
          style={{ left: `calc(${(split / weeks.length) * 100}% - 5px)`, borderColor: 'color-mix(in srgb, var(--color-text-muted) 70%, transparent)' }} />}
      </div>
      <div className={`mt-2 flex gap-2.5 text-meta ${DIM}`} aria-hidden>
        {weeks.map((w) => <span key={w.week} className="flex-1 whitespace-nowrap text-center">{w.start.slice(5).replace('-', '/')}</span>)}
      </div>
    </>
  );
}

function Chain({ audit }: { audit: NonNullable<CognitionLevelData['audit']> }) {
  const { t } = useTranslation();
  const total = audit.total || 1;
  const present = OUTCOMES.filter((o) => (audit.outcomes[o] ?? 0) > 0);
  const top = [...present].sort((a, b) => (audit.outcomes[b] ?? 0) - (audit.outcomes[a] ?? 0))[0];
  const grown = useGrown(`${audit.month}:${audit.total}`);
  return (
    <div className="mt-5 border-t border-[var(--color-border)] pt-4" data-testid="level-chain">
      <div className="flex flex-wrap items-baseline gap-x-2.5 text-body">
        <b className="font-semibold">{t('cogLevel.chain.title')}</b>
        {top && <span className={MUTED}>{t('cogLevel.chain.mostly', { stage: t(`cogLevel.cause.${STAGE_KEY[top]}`), pct: pct((audit.outcomes[top] ?? 0) / total) })}</span>}
        <span className={`ml-auto text-small ${DIM}`}>{t(audit.baseline ? 'cogLevel.chain.baseline' : 'cogLevel.chain.month', { label: audit.month, month: audit.month, n: audit.total })}</span>
      </div>
      <div className="mt-2.5 flex h-2.5 overflow-hidden rounded-[5px] bg-[var(--color-bg-chrome)]" role="img"
        aria-label={present.map((o) => `${t(`cogLevel.cause.${STAGE_KEY[o]}`)} ${audit.outcomes[o]}`).join(', ')}>
        {present.map((o) => (
          <i key={o} className="so-width block h-full" style={{ width: grown ? `${((audit.outcomes[o] ?? 0) / total) * 100}%` : '0%', background: STAGE_COLOR[STAGE_KEY[o]] }} />
        ))}
      </div>
      <div className={`mt-2 flex flex-wrap gap-x-4 gap-y-1 text-small ${MUTED}`}>
        {present.map((o) => (
          <span key={o} className="inline-flex items-center gap-1.5 whitespace-nowrap" data-testid={`level-stage-${STAGE_KEY[o]}`}>
            <i className="inline-block h-2 w-2 rounded-sm" style={{ background: STAGE_COLOR[STAGE_KEY[o]] }} />
            {t(`cogLevel.cause.${STAGE_KEY[o]}`)} <b className="font-semibold tabular-nums text-[var(--color-text)]">{audit.outcomes[o]}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export function CognitionLevel() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ['ingestion', 'cognition-level'], queryFn: () => ingestionService.cognitionLevel() });
  if (!q.data) return null;   // the queue above stands alone while this loads or fails
  const data = q.data;
  const first = data.weeks.findIndex((w) => w.notCorrectedRate != null);
  const weeks = first < 0 ? [] : data.weeks.slice(first);
  const trend = data.trend;
  const now = trend?.now.rate != null ? 1 - trend.now.rate : null;
  const before = trend?.before.rate != null ? 1 - trend.before.rate : null;
  const thisWeek = data.weeks[data.weeks.length - 1];
  const dir = trend?.direction;
  const diff = now != null && before != null ? pct(now) - pct(before) : null;
  return (
    <section className="mt-10" data-testid="cognition-level">
      <ZoneLabel>{t('cogLevel.title')}</ZoneLabel>
      <div className={`${CARD} px-[18px] py-4`}>
        {!weeks.length || now == null ? (
          <p className={`text-small ${DIM}`} data-testid="level-line">{weeks.length ? levelLine(t, trend) : t('cogLevel.empty')}</p>
        ) : (
          <>
            <Big testid="level-big" value={String(pct(now))} unit="%" what={t('cogLevel.notCorrected')}
              of={t('cogLevel.of', { n: trend?.now.judged ?? 0 })}
              compare={before == null ? undefined : (
                <span title={levelLine(t, trend)} data-testid="level-line">
                  <Compare testid="level-compare"
                    change={diff && (dir === 'better' || dir === 'worse') ? t('cogLevel.pts', { sign: diff > 0 ? '▲' : '▼', count: Math.abs(diff) }) : undefined}
                    good={dir === 'better'} was={t('cogLevel.was', { pct: pct(before) })} />
                  <span className="sr-only">{levelLine(t, trend)}</span>
                </span>
              )} />
            <Weeks weeks={weeks} marks={data.marks} recent={Math.min(trend?.weeks ?? 4, weeks.length)} />
          </>
        )}
        <Kpis testid="level-kpis" items={[
          { key: 'week', label: t('cogLevel.kpi.week'),
            value: thisWeek?.notCorrectedRate != null
              ? <>{pct(thisWeek.notCorrectedRate)}%<small className={`ml-1 text-small font-medium ${MUTED}`}>{t('cogLevel.kpi.conversations', { count: thisWeek.judged })}</small></>
              : <span className={`text-body ${MUTED}`}>{t('cogLevel.thisWeekNone')}</span> },
          { key: 'repeats', label: t('cogLevel.kpi.repeats'), warn: !!thisWeek?.repeats,
            value: thisWeek?.corrections ? `${thisWeek.repeats} / ${thisWeek.corrections}` : '—' },
          { key: 'used', label: t('cogLevel.kpi.used', { days: data.used.windowDays }),
            value: data.used.usedRate == null ? '—' : `${pct(data.used.usedRate)}%` },
        ]} />
        {data.audit ? <Chain audit={data.audit} /> : (
          <p className={`mt-5 text-small ${DIM}`}>{t('cogLevel.chain.none')}</p>
        )}
      </div>
    </section>
  );
}
