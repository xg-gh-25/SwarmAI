/**
 * Swarm's cognition level — under the queue in the first Needs You tab (design
 * docs/design/2026-10-31-cognition-level.md). It answers one question: is Swarm needing
 * the person less? Read top to bottom:
 *
 * - one sentence and the share of this week's conversations the person did not have to
 *   correct, with how many of this week's corrections repeat an earlier one;
 * - a weekly curve of both, back-filled weeks dashed, with marks where the rules the
 *   agent reads changed. A trend is claimed only between weeks measured the same way;
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

const GOOD = 'var(--color-zone-cognitive)';
const WARN = 'var(--color-status-warning)';
const ERR = 'var(--color-status-error)';
const pct = (v: number) => `${Math.round(v * 100)}%`;
const OUTCOMES = ['事先没有', '有但没送到', '送到仍犯', 'DDD 内容过时'] as const;
const STAGE_KEY: Record<(typeof OUTCOMES)[number], string> = {
  事先没有: 'missing', 有但没送到: 'notDelivered', 送到仍犯: 'usedWrong', 'DDD 内容过时': 'stale',
};

const sameWay = (a: LevelWeek, b: LevelWeek) => a.source === b.source;
/** A week's start (``YYYY-MM-DD``) as local midnight, which is what the backend means. */
const localDay = (day: string) => new Date(`${day}T00:00:00`).getTime();

/** The headline sentence: a trend only between weeks measured the same way. */
export function levelLine(t: TFunction, weeks: LevelWeek[]): string {
  const rated = weeks.filter((w) => w.notCorrectedRate != null);
  if (!rated.length) return t('cogLevel.line.none');
  const last = rated[rated.length - 1];
  const before = [...rated.slice(0, -1)].reverse().find((w) => sameWay(w, last));
  if (!before) return t('cogLevel.line.notComparable');
  const delta = (last.notCorrectedRate ?? 0) - (before.notCorrectedRate ?? 0);
  if (delta >= 0.05) return t('cogLevel.line.better');
  if (delta <= -0.05) return t('cogLevel.line.worse');
  return t('cogLevel.line.flat');
}

function Curve({ weeks, marks, t }: { weeks: LevelWeek[]; marks: CognitionLevelData['marks']; t: TFunction }) {
  const W = 760, H = 210, L = 40, R = 750, T = 16, B = 186;
  const WEEK = 7 * 864e5;
  // one slot per week, each point at the middle of its week, so a mark keeps its place
  // inside the week it happened in (also the last one)
  const slot = (R - L) / Math.max(1, weeks.length);
  const x = (i: number) => L + (i + 0.5) * slot;
  const t0 = weeks.length ? localDay(weeks[0].start) : 0;
  const y = (v: number) => B - v * (B - T);
  /** One segment per pair of neighbouring weeks that both have a value; dashed unless
   *  both ends are fully recorded (a back-fill is measured another way). */
  const segments = (pick: (w: LevelWeek) => number | null) => weeks.slice(1).map((w, k) => {
    const a = pick(weeks[k]);
    const b = pick(w);
    if (a == null || b == null) return null;
    return { key: w.week, x1: x(k), y1: y(a), x2: x(k + 1), y2: y(b),
             dashed: weeks[k].source !== 'recorded' || w.source !== 'recorded' };
  }).filter((g): g is NonNullable<typeof g> => g != null);
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
        return (
          <g key={`${m.at}-${m.label}`}>
            <line x1={mx} x2={mx} y1={T - 4} y2={B} stroke="var(--color-text-muted)" strokeDasharray="3 4" />
            <text x={mx > R - 180 ? mx - 4 : mx + 4} textAnchor={mx > R - 180 ? 'end' : 'start'} y={T + 8}
              fontSize={11} fill="var(--color-text-muted)">
              {m.kind === 'record' ? t('cogLevel.mark.review') : m.kind === 'version' ? t('cogLevel.mark.version', { v: m.label }) : m.label}
            </text>
          </g>
        );
      })}
      {segments((w) => w.notCorrectedRate).map((g) => (
        <line key={`n${g.key}`} x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2} stroke={GOOD} strokeWidth={2.5}
          strokeDasharray={g.dashed ? '6 5' : undefined} />
      ))}
      {weeks.map((w, i) => w.notCorrectedRate != null && (
        <circle key={`c${w.week}`} cx={x(i)} cy={y(w.notCorrectedRate)} r={3.5} fill={GOOD}>
          <title>{t('cogLevel.point', { week: w.start, rate: pct(w.notCorrectedRate), n: w.known })}</title>
        </circle>
      ))}
      {weeks.map((w, i) => w.repeatRate != null && (
        <circle key={`r${w.week}`} cx={x(i)} cy={y(w.repeatRate)} r={3.5} fill={WARN}>
          <title>{t('cogLevel.repeatPoint', { week: w.start, repeats: w.repeats, corrections: w.corrections })}</title>
        </circle>
      ))}
      {segments((w) => w.repeatRate).map((g) => (
        <line key={`r${g.key}`} x1={g.x1} y1={g.y1} x2={g.x2} y2={g.y2} stroke={WARN} strokeWidth={2} />
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
  const first = data.weeks.findIndex((w) => w.notCorrectedRate != null || w.repeatRate != null);
  const weeks = first < 0 ? [] : data.weeks.slice(first);
  const latest = data.latest;
  const thisWeek = data.weeks[data.weeks.length - 1];
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
            {latest?.notCorrectedRate != null && (
              <>
                <div className="text-[44px] font-semibold leading-tight tabular-nums" style={{ color: GOOD }}>
                  {pct(latest.notCorrectedRate)}
                </div>
                <div className="text-body text-[var(--color-text-muted)]">{t('cogLevel.notCorrected')}</div>
                <div className="mt-1 text-meta text-[var(--color-text-muted)]">
                  {t('cogLevel.basis', { n: latest.known, week: latest.start })}
                  {latest.source !== 'recorded' && ` · ${t(`cogLevel.source.${latest.source}`)}`}
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
              <span><i className="mr-1.5 inline-block h-0.5 w-3.5 align-middle" style={{ background: GOOD }} />{t('cogLevel.legend.notCorrected')}</span>
              <span><i className="mr-1.5 inline-block h-0.5 w-3.5 align-middle" style={{ background: WARN }} />{t('cogLevel.legend.repeats')}</span>
              <span><i className="mr-1.5 inline-block w-3.5 align-middle" style={{ borderTop: `2px dashed ${GOOD}` }} />{t('cogLevel.legend.backfilled')}</span>
              <span><i className="mr-1.5 inline-block w-3.5 align-middle" style={{ borderTop: '1px dashed var(--color-text-muted)' }} />{t('cogLevel.legend.marks')}</span>
            </div>
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
