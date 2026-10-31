/**
 * Swarm's cognition level — under the queue in the first Needs You tab (design
 * docs/design/2026-10-31-cognition-level.md). It answers one question: is Swarm needing
 * the person less? Read top to bottom:
 *
 * - one sentence and the share of the last four weeks' conversations the person did not
 *   have to correct (this week's beside it), and how many of this week's corrections
 *   repeat an earlier one;
 * - a weekly curve: each week a point, the four-week rolling share as the line (one week
 *   is too few conversations to read a trend from), marks where the rules the agent reads
 *   changed. Every week is measured the same way (one judge over the person's own words),
 *   and the review's own record checks that judge;
 * - where mistakes come from (the monthly mistake audit), as one chain: was the
 *   knowledge there, was it delivered, was it used right, was it still true.
 *
 * Data: GET /ingestion/cognition-level (backend core/ingestion/cognition_level.level).
 * Measured outcomes only, never how much is stored.
 *
 * Key export: `CognitionLevel`.
 */
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { ingestionService, type CognitionLevelData, type LevelWeek } from '../../services/ingestion';
import { SectionLabel } from '../overlay';
import { rolling, levelLine } from './cognitionLevelText';

const GOOD = 'var(--color-zone-cognitive)';
const WARN = 'var(--color-status-warning)';
const ERR = 'var(--color-status-error)';
const pct = (v: number) => `${Math.round(v * 100)}%`;
const OUTCOMES = ['事先没有', '有但没送到', '送到仍犯', 'DDD 内容过时'] as const;
const STAGE_KEY: Record<(typeof OUTCOMES)[number], string> = {
  事先没有: 'missing', 有但没送到: 'notDelivered', 送到仍犯: 'usedWrong', 'DDD 内容过时': 'stale',
};
/** A week's start (``YYYY-MM-DD``) as local midnight, which is what the backend means. */
const localDay = (day: string) => new Date(`${day}T00:00:00`).getTime();

function Curve({ weeks, marks, t }: { weeks: LevelWeek[]; marks: CognitionLevelData['marks']; t: TFunction }) {
  const W = 760, H = 210, L = 40, R = 750, T = 16, B = 186;
  const WEEK = 7 * 864e5;
  // one slot per week, each point at the middle of its week, so a mark keeps its place
  // inside the week it happened in (also the last one)
  const slot = (R - L) / Math.max(1, weeks.length);
  const x = (i: number) => L + (i + 0.5) * slot;
  const y = (v: number) => B - v * (B - T);
  const t0 = weeks.length ? localDay(weeks[0].start) : 0;
  const trend = rolling(weeks);
  const line = trend.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean).join(' ');
  const markX = (at: string) => {
    const offset = (new Date(at).getTime() - t0) / WEEK;
    return offset < 0 || offset > weeks.length ? null : L + offset * slot;
  };
  return (
    <svg viewBox={`0 0 ${W} ${H + 20}`} className="block w-full" role="img" data-testid="level-curve"
      aria-label={weeks.map((w) => `${w.week} ${w.notCorrectedRate == null ? '-' : pct(w.notCorrectedRate)}`).join(', ')}>
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line x1={L} x2={R} y1={y(v)} y2={y(v)} stroke="var(--color-border)" strokeWidth={1} />
          <text x={4} y={y(v) + 4} fontSize={11} fill="var(--color-text-dim)">{pct(v)}</text>
        </g>
      ))}
      {weeks.map((w, i) => (
        <text key={w.week} x={x(i)} y={H + 14} fontSize={11} textAnchor="middle" fill="var(--color-text-dim)">
          {w.start.slice(5)}
        </text>
      ))}
      {marks.map((m) => {
        const mx = markX(m.at);
        if (mx == null) return null;
        const right = mx > R - 180;
        return (
          <g key={`${m.at}-${m.label}`}>
            <line x1={mx} x2={mx} y1={T - 4} y2={B} stroke="var(--color-text-muted)" strokeDasharray="3 4" />
            <text x={right ? mx - 4 : mx + 4} textAnchor={right ? 'end' : 'start'} y={T + 8} fontSize={11}
              fill="var(--color-text-muted)">
              {m.kind === 'record' ? t('cogLevel.mark.review') : m.kind === 'version' ? t('cogLevel.mark.version', { v: m.label }) : m.label}
            </text>
          </g>
        );
      })}
      {line && <polyline points={line} fill="none" stroke={GOOD} strokeWidth={3} data-testid="level-trend" />}
      {weeks.map((w, i) => w.notCorrectedRate != null && (
        <circle key={`c${w.week}`} cx={x(i)} cy={y(w.notCorrectedRate)} r={4} fill={GOOD} fillOpacity={0.45}>
          <title>{t('cogLevel.point', { week: w.start, rate: pct(w.notCorrectedRate), n: w.judged })}</title>
        </circle>
      ))}
      {weeks.map((w, i) => w.repeatRate != null && (
        <circle key={`r${w.week}`} cx={x(i)} cy={y(w.repeatRate)} r={3.5} fill={WARN}>
          <title>{t('cogLevel.repeatPoint', { week: w.start, repeats: w.repeats, corrections: w.corrections })}</title>
        </circle>
      ))}
    </svg>
  );
}

function Chain({ audit, t }: { audit: NonNullable<CognitionLevelData['audit']>; t: TFunction }) {
  const total = audit.total || 1;
  const top = Math.max(...OUTCOMES.map((o) => audit.outcomes[o] ?? 0));
  return (
    <div className="mt-9" data-testid="level-chain">
      <div className="text-body font-semibold text-[var(--color-text)]">{t('cogLevel.chain.title')}</div>
      <p className="mb-4 mt-1 text-meta text-[var(--color-text-muted)]">
        {t('cogLevel.chain.rule')} {audit.baseline ? t('cogLevel.chain.baseline', { label: audit.month, n: audit.total })
          : t('cogLevel.chain.month', { month: audit.month, n: audit.total })}
      </p>
      <div className="grid grid-cols-4">
        {OUTCOMES.map((o, i) => {
          const n = audit.outcomes[o] ?? 0;
          const share = n / total;
          const tone = n === top && n > 0 ? ERR : share >= 0.2 ? WARN : 'var(--color-text-muted)';
          const prev = audit.previous?.outcomes[o];
          return (
            <div key={o} className="relative px-4 pb-3.5 pt-3" style={{ borderTop: `3px solid ${tone}` }}
              data-testid={`level-stage-${STAGE_KEY[o]}`}>
              {i > 0 && <span className="absolute -left-2 -top-3.5 text-[var(--color-text-dim)]">→</span>}
              <div className="font-semibold text-[var(--color-text)]">{t(`cogLevel.stage.${STAGE_KEY[o]}`)}</div>
              <div className="text-meta text-[var(--color-text-dim)]">{t(`cogLevel.stageQ.${STAGE_KEY[o]}`)}</div>
              <div className="mt-3 text-[28px] font-semibold tabular-nums"
                style={{ color: tone === 'var(--color-text-muted)' ? 'var(--color-text)' : tone }}>
                {n}<span className="ml-1 text-meta font-normal text-[var(--color-text-muted)]">
                  {t('cogLevel.chain.count', { pct: pct(share) })}
                  {prev != null && ` · ${t('cogLevel.chain.prev', { n: prev })}`}
                </span>
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">{t(`cogLevel.stageFix.${STAGE_KEY[o]}`)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CognitionLevel({ onOpenHealth }: { onOpenHealth?: () => void }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ['ingestion', 'cognition-level'], queryFn: () => ingestionService.cognitionLevel() });
  if (!q.data) return null;   // the queue above stands alone while this loads or fails
  const data = q.data;
  const first = data.weeks.findIndex((w) => w.notCorrectedRate != null);
  const weeks = first < 0 ? [] : data.weeks.slice(first);
  const trend = rolling(weeks);
  const recent = trend[trend.length - 1];
  const thisWeek = data.weeks[data.weeks.length - 1];
  const ag = data.agreement;
  return (
    <section className="mt-12" data-testid="cognition-level">
      <SectionLabel right={t('cogLevel.hint')}>{t('cogLevel.title')}</SectionLabel>
      {!weeks.length ? (
        <p className="mt-3 text-small text-[var(--color-text-dim)]">{t('cogLevel.empty')}</p>
      ) : (
        <div className="mt-3 grid grid-cols-[300px_1fr] items-start gap-9">
          <div>
            <p className="mb-3.5 text-[20px] font-semibold leading-snug text-[var(--color-text)]" data-testid="level-line">
              {levelLine(t, weeks)}
            </p>
            {recent != null && (
              <>
                <div className="text-[44px] font-semibold leading-tight tabular-nums" style={{ color: GOOD }}>
                  {pct(recent)}
                </div>
                <div className="text-body text-[var(--color-text-muted)]">{t('cogLevel.notCorrected')}</div>
                <div className="mt-1 text-meta text-[var(--color-text-muted)]">
                  {thisWeek.notCorrectedRate != null
                    ? t('cogLevel.thisWeek', { rate: pct(thisWeek.notCorrectedRate), n: thisWeek.judged })
                    : t('cogLevel.thisWeekNone')}
                </div>
              </>
            )}
            <div className="mt-5">
              <div className="text-[24px] font-semibold tabular-nums" style={{ color: WARN }}>
                {thisWeek.corrections ? `${thisWeek.repeats} / ${thisWeek.corrections}` : '—'}
              </div>
              <div className="text-meta text-[var(--color-text-muted)]">{t('cogLevel.repeats')}</div>
            </div>
          </div>
          <div>
            <Curve weeks={weeks} marks={data.marks} t={t} />
            <div className="ml-1 mt-0.5 flex flex-wrap gap-4 text-meta text-[var(--color-text-muted)]">
              <span><i className="mr-1.5 inline-block h-[3px] w-3.5 align-middle" style={{ background: GOOD }} />{t('cogLevel.legend.trend')}</span>
              <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: GOOD, opacity: 0.45 }} />{t('cogLevel.legend.week')}</span>
              <span><i className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: WARN }} />{t('cogLevel.legend.repeats')}</span>
              <span><i className="mr-1.5 inline-block w-3.5 align-middle" style={{ borderTop: '1px dashed var(--color-text-muted)' }} />{t('cogLevel.legend.marks')}</span>
            </div>
            <p className="ml-1 mt-2 text-meta text-[var(--color-text-dim)]" data-testid="level-agreement">
              {t('cogLevel.how')}{' '}
              {ag.correctedTotal ? t('cogLevel.agreeCorrected', { a: ag.correctedAgreed, n: ag.correctedTotal }) : t('cogLevel.agreeNone')}
              {ag.cleanTotal > 0 && ` ${t('cogLevel.agreeClean', { a: ag.cleanAgreed, n: ag.cleanTotal })}`}
            </p>
          </div>
        </div>
      )}
      {data.audit ? <Chain audit={data.audit} t={t} /> : (
        <p className="mt-6 text-meta text-[var(--color-text-dim)]">{t('cogLevel.chain.none')}</p>
      )}
      <div className="mt-4 flex justify-between text-meta text-[var(--color-text-dim)]">
        <span>{data.used.usedRate == null ? t('cogLevel.usedNone', { days: data.used.windowDays })
          : t('cogLevel.used', { pct: pct(data.used.usedRate), days: data.used.windowDays })}</span>
        {onOpenHealth && (
          <button type="button" onClick={onOpenHealth} className="text-[var(--color-primary)] hover:underline"
            data-testid="level-open-health">{t('cogLevel.openHealth')}</button>
        )}
      </div>
    </section>
  );
}
