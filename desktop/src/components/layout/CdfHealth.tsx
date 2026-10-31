/**
 * Health — the cognition system's health inside the Need You overlay (design
 * docs/design/2026-10-31-cdf-health.md). Read top to bottom:
 *
 * - one sentence, then three numbers, each against something: is what was kept useful
 *   (vs its target), is CDF itself healthy (checks passed of those measured), what it
 *   costs (its share of the system's spend, per useful entry);
 * - the details of each: value per destination (a click shows the sampled entries with
 *   the judge's reason; the person can set their own verdict), the checks, the spend;
 * - last, the judge's reading.
 *
 * Data: GET /ingestion/cognition-health (backend core/ingestion/health.overview). The
 * judge only scores; nothing here removes an entry. "No data" is never shown as a pass.
 *
 * Key export: `CdfHealth`.
 */
import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ingestionService,
  type CognitionHealth,
  type HealthCheck,
  type HealthDestination,
  type HealthVerdict,
  type HealthWindow,
  type WeekRate,
} from '../../services/ingestion';
import { SectionLabel, Segmented } from '../overlay';
import { OverlayError, OverlayLoading } from './OverlayState';

const KEPT = 'var(--color-zone-cognitive)';
const WARN = 'var(--color-status-warning)';
const ERR = 'var(--color-status-error)';
const DIM = 'var(--color-text-dim)';
const WEAK = 'color-mix(in srgb, var(--color-status-warning) 55%, transparent)';
const TRACK = 'color-mix(in srgb, var(--color-border) 60%, transparent)';
const GROUPS: HealthDestination['group'][] = ['every_turn', 'on_demand', 'other'];

const pct = (v: number) => `${Math.round(v * 100)}%`;
const usd = (v: number) => (v >= 100 ? `$${Math.round(v)}` : v >= 1 ? `$${v.toFixed(1)}` : `$${v.toFixed(2)}`);
const rateTone = (rate: number | null, target: number) =>
  rate == null ? DIM : rate >= target ? KEPT : rate >= target - 0.2 ? WARN : ERR;

function Spark({ values, color, testid }: { values: Array<number | null>; color: string; testid?: string }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] != null);
  if (pts.length < 2) return <div className="mt-2 h-7 text-meta text-[var(--color-text-dim)]" data-testid={testid} />;
  const max = Math.max(...pts.map((p) => p[1]), 1e-9);
  const min = Math.min(...pts.map((p) => p[1]));
  const span = max - min || 1;
  const n = Math.max(1, values.length - 1);
  const line = pts.map(([i, v]) => `${(i / n) * 100},${26 - ((v - min) / span) * 22}`).join(' ');
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="mt-2 block h-7 w-56" data-testid={testid}>
      <polyline fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" points={line} />
    </svg>
  );
}

function Indicator({ question, big, unit, tone, note, spark }: {
  question: string; big: string; unit: string; tone: string; note: ReactNode; spark: ReactNode;
}) {
  return (
    <div className="min-w-0 px-7 first:pl-0 [&+&]:border-l [&+&]:border-[var(--color-border)]">
      <div className="text-small text-[var(--color-text-muted)]">{question}</div>
      <div className="mt-1 text-[34px] font-semibold leading-tight tabular-nums" style={{ color: tone }}>
        {big}<span className="ml-1.5 text-body font-normal text-[var(--color-text-muted)]">{unit}</span>
      </div>
      <div className="text-meta text-[var(--color-text-muted)]">{note}</div>
      {spark}
    </div>
  );
}

function verdictLine(t: (k: string, o?: Record<string, unknown>) => string, data: CognitionHealth): string {
  const failing = data.checks.filter((c) => c.status === 'fail').length;
  const { rate, target } = data.value;
  const parts: string[] = [];
  if (failing) parts.push(t('cogHealth.line.checksFail', { count: failing }));
  if (rate != null && rate < target) parts.push(t('cogHealth.line.valueLow', { pct: pct(1 - rate) }));
  if (!parts.length) return rate == null ? t('cogHealth.line.notEnough') : t('cogHealth.line.ok');
  return parts.join(t('cogHealth.line.and'));
}

export function CdfHealth() {
  const { t, i18n } = useTranslation();
  const zh = (i18n.language || '').startsWith('zh');
  const [window, setWindow] = useState<HealthWindow>('7d');
  const client = useQueryClient();
  const q = useQuery({
    queryKey: ['ingestion', 'cognition-health', window],
    queryFn: () => ingestionService.cognitionHealth(window),
    refetchInterval: (query) => (query.state.data?.running ? 5000 : false),
  });
  const recheck = useMutation({
    mutationFn: () => ingestionService.recheckHealth(),
    onSettled: () => { void client.invalidateQueries({ queryKey: ['ingestion', 'cognition-health'] }); },
  });

  if (q.isPending) return <OverlayLoading />;
  if (q.isError || !q.data) {
    return <OverlayError testid="health-load-error" retryTestid="health-load-retry"
      message={t('cogHealth.loadFailed')} onRetry={() => { void q.refetch(); }} />;
  }
  const data = q.data;
  const measured = data.checks.filter((c) => c.status !== 'no_data');
  const passed = measured.filter((c) => c.status === 'pass').length;
  const noData = data.checks.length - measured.length;
  const failing = measured.length - passed;
  const rate = data.value.rate;
  const running = data.running || recheck.isPending;

  return (
    <div className="flex flex-col gap-12 py-4" data-testid="cdf-health">
      <section>
        <div className="flex items-start gap-5">
          <p className="m-0 flex-1 text-[20px] font-semibold text-[var(--color-text)]" data-testid="health-line">
            {verdictLine(t, data)}
          </p>
          <Segmented<HealthWindow> value={window} onChange={setWindow}
            options={[{ key: '7d', label: t('cogHealth.w7d') }, { key: '30d', label: t('cogHealth.w30d') }]} />
          <button type="button" data-testid="health-recheck" disabled={running}
            onClick={() => recheck.mutate()}
            className="h-7 rounded-md border border-[var(--color-border)] px-3 text-small text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] disabled:opacity-50">
            {running ? t('cogHealth.running') : t('cogHealth.recheck')}
          </button>
        </div>
        <div className="mt-1 text-meta text-[var(--color-text-dim)]">
          {data.generatedAt ? t('cogHealth.generatedAt', { at: new Date(data.generatedAt).toLocaleString(i18n.language) })
            : t('cogHealth.neverRun')}
        </div>
        <div className="mt-6 grid grid-cols-3">
          <Indicator question={t('cogHealth.q.value')}
            big={rate == null ? '—' : pct(rate)} unit={rate == null ? t('cogHealth.notEnough') : t('cogHealth.useful')}
            tone={rateTone(rate, data.value.target)}
            note={<>
              {t('cogHealth.valueNote', { target: pct(data.value.target), scored: data.value.scored })}
              {data.used.usedRate != null && <span className="block">{t('cogHealth.usedNote', { pct: pct(data.used.usedRate) })}</span>}
            </>}
            spark={<Spark values={data.value.trend.map((w) => w.rate)} color={rateTone(rate, data.value.target)} />} />
          <Indicator question={t('cogHealth.q.checks')}
            big={measured.length ? `${passed} / ${measured.length}` : '—'} unit={t('cogHealth.passed')}
            tone={!measured.length ? DIM : failing ? ERR : KEPT}
            note={t('cogHealth.checksNote', { failing, noData })}
            spark={<Spark values={data.checksTrend.map((p) => (p.measured ? p.passed / p.measured : null))}
              color={failing ? ERR : KEPT} />} />
          <Indicator question={t('cogHealth.q.cost')}
            big={usd(data.cost.cdfUsd)} unit={t(`cogHealth.per${window}`)} tone="var(--color-text)"
            note={<>
              {data.cost.share != null && t('cogHealth.costShare', { pct: pct(data.cost.share) })}
              {data.cost.perUsefulUsd != null && ` · ${t('cogHealth.perUseful', { usd: usd(data.cost.perUsefulUsd) })}`}
            </>}
            spark={<Spark values={data.cost.days.map((d) => d.cdfUsd)} color={WARN} />} />
        </div>
      </section>

      <ValueSection data={data} zh={zh} />
      <ChecksSection checks={data.checks} />
      <CostSection data={data} />
      <ReadingSection data={data} zh={zh} />
    </div>
  );
}

function Bar({ d }: { d: HealthDestination }) {
  if (d.rate == null) return <div />;
  const total = d.useful + d.weak + d.withdraw || 1;
  return (
    <div className="relative flex h-2 overflow-hidden rounded-sm" style={{ background: TRACK }}>
      <i style={{ width: `${(d.useful / total) * 100}%`, background: KEPT }} />
      <i style={{ width: `${(d.weak / total) * 100}%`, background: WEAK }} />
      <i style={{ width: `${(d.withdraw / total) * 100}%`, background: ERR }} />
      <span className="absolute -bottom-1 -top-1 w-px opacity-50" style={{ left: `${d.target * 100}%`, background: 'var(--color-text)' }} />
    </div>
  );
}

function MiniTrend({ trend, color }: { trend: WeekRate[]; color: string }) {
  const pts = trend.map((w, i) => [i, w.rate] as const).filter((p): p is readonly [number, number] => p[1] != null);
  if (pts.length < 2) return <span />;
  const n = Math.max(1, trend.length - 1);
  return (
    <svg viewBox="0 0 60 18" className="h-[18px] w-[60px]">
      <polyline fill="none" stroke={color} strokeWidth={1.5}
        points={pts.map(([i, v]) => `${(i / n) * 60},${16 - v * 14}`).join(' ')} />
    </svg>
  );
}

function ValueSection({ data, zh }: { data: CognitionHealth; zh: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section data-testid="health-value">
      <SectionLabel right={t('cogHealth.value.clickHint')}>{t('cogHealth.q.value')}</SectionLabel>
      <p className="mb-4 mt-1 max-w-[900px] text-meta text-[var(--color-text-dim)]">{t('cogHealth.value.rule')}</p>
      {GROUPS.map((group) => (
        <div key={group} className="mb-4">
          <div className="mb-1 ml-2.5 flex items-baseline gap-2.5">
            <span className="font-semibold text-[var(--color-text)]">{t(`cogHealth.group.${group}`)}</span>
            <span className="text-meta text-[var(--color-text-dim)]">{t(`cogHealth.groupHint.${group}`)}</span>
          </div>
          {data.value.destinations.filter((d) => d.group === group).map((d) => (
            <div key={d.key}>
              <button type="button" data-testid={`health-dest-${d.key}`}
                onClick={() => setOpen(open === d.key ? null : d.key)}
                className="grid w-full grid-cols-[240px_1fr_80px_64px_140px] items-center gap-4 rounded-md px-2.5 py-1.5 text-left hover:bg-[var(--color-hover)]">
                <span className="min-w-0">
                  <span className="block text-[var(--color-text)]">{t(`cogHealth.dest.${d.key}`)}</span>
                  <span className="block text-meta text-[var(--color-text-dim)]">{t(`cogHealth.destHint.${d.key}`)}</span>
                </span>
                <Bar d={d} />
                <span className="text-right font-semibold tabular-nums" style={{ color: rateTone(d.rate, d.target) }}>
                  {d.rate == null ? <span className="text-meta font-normal">{t('cogHealth.notEnough')}</span> : pct(d.rate)}
                </span>
                <MiniTrend trend={d.trend} color={rateTone(d.rate, d.target)} />
                <span className="text-right text-meta tabular-nums text-[var(--color-text-dim)]">
                  {t('cogHealth.value.counts', { scored: d.scored, written: d.written })}
                </span>
              </button>
              {open === d.key && <Samples d={d} zh={zh} />}
            </div>
          ))}
        </div>
      ))}
      <div className="ml-2.5 flex gap-4 text-meta text-[var(--color-text-dim)]">
        {(['useful', 'weak', 'withdraw'] as const).map((v) => (
          <span key={v}><i className="mr-1.5 inline-block h-2 w-2.5 rounded-sm align-middle"
            style={{ background: v === 'useful' ? KEPT : v === 'weak' ? WEAK : ERR }} />{t(`cogHealth.verdict.${v}`)}</span>
        ))}
        <span>{t('cogHealth.value.minNote', { n: 10 })}</span>
      </div>
    </section>
  );
}

function Samples({ d, zh }: { d: HealthDestination; zh: boolean }) {
  const { t } = useTranslation();
  const client = useQueryClient();
  const set = useMutation({
    mutationFn: ({ unitId, verdict }: { unitId: string; verdict: HealthVerdict }) =>
      ingestionService.setHealthVerdict(unitId, verdict),
    onSettled: () => { void client.invalidateQueries({ queryKey: ['ingestion', 'cognition-health'] }); },
  });
  if (!d.samples.length) {
    return <div className="mb-2 ml-2.5 border-l-2 border-[var(--color-border)] px-3.5 py-2 text-meta text-[var(--color-text-dim)]">
      {t('cogHealth.value.noSamples')}</div>;
  }
  return (
    <div className="mb-2 ml-2.5 border-l-2 border-[var(--color-border)] px-3.5 py-2" data-testid={`health-samples-${d.key}`}>
      <div className="mb-1.5 text-meta text-[var(--color-text-muted)]">{t('cogHealth.value.samplesHint')}</div>
      {d.samples.map((s) => {
        const shown = s.userVerdict ?? s.verdict;
        return (
          <div key={s.unitId} className="grid grid-cols-[64px_1fr_auto] gap-2.5 py-1">
            <span className="text-meta font-semibold"
              style={{ color: shown === 'useful' ? KEPT : shown === 'weak' ? WARN : ERR }}>
              {t(`cogHealth.verdict.${shown}`)}
              {s.userVerdict && <span className="block font-normal text-[var(--color-text-dim)]">{t('cogHealth.value.byYou')}</span>}
            </span>
            <span className="min-w-0">
              <span className="block text-[var(--color-text)]">{s.title}{s.project && <span className="text-[var(--color-text-dim)]"> · {s.project}</span>}</span>
              <span className="block text-meta text-[var(--color-text-muted)]">
                {s.userVerdict && <>{t('cogHealth.value.judgeSaid', { verdict: t(`cogHealth.verdict.${s.verdict}`) })} · </>}
                {s.klass && s.verdict === 'withdraw' && <>{t(`cogHealth.klass.${s.klass}`)} · </>}{zh ? s.reasonZh || s.reasonEn : s.reasonEn || s.reasonZh}
              </span>
            </span>
            <span className="flex gap-1">
              {(['useful', 'weak', 'withdraw'] as const).map((v) => (
                <button key={v} type="button" data-testid={`health-verdict-${s.unitId}-${v}`}
                  disabled={set.isPending}
                  onClick={() => set.mutate({ unitId: s.unitId, verdict: v })}
                  aria-pressed={s.userVerdict === v}
                  className="h-6 rounded border border-[var(--color-border)] px-2 text-meta text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] aria-pressed:text-[var(--color-text)]">
                  {t(`cogHealth.verdict.${v}`)}
                </button>
              ))}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function checkValue(t: (k: string, o?: Record<string, unknown>) => string, c: HealthCheck): string {
  if (c.status === 'no_data' || !c.value) return t('cogHealth.noData');
  return t(`cogHealth.checkValue.${c.id}`, c.value as Record<string, unknown>);
}

function ChecksSection({ checks }: { checks: HealthCheck[] }) {
  const { t } = useTranslation();
  return (
    <section data-testid="health-checks">
      <SectionLabel>{t('cogHealth.q.checks')}</SectionLabel>
      <p className="mb-3 mt-1 text-meta text-[var(--color-text-dim)]">{t('cogHealth.checks.rule')}</p>
      {!checks.length && <p className="ml-2.5 text-meta text-[var(--color-text-dim)]">{t('cogHealth.neverRun')}</p>}
      {checks.length > 0 && (
        <div className="grid grid-cols-[14px_240px_1fr_280px] gap-x-2.5 px-2.5 py-1.5 text-meta text-[var(--color-text-dim)]">
          <span /><span>{t('cogHealth.checks.name')}</span><span>{t('cogHealth.checks.measured')}</span>
          <span className="text-right">{t('cogHealth.checks.requirement')}</span>
        </div>
      )}
      {checks.map((c) => (
        <div key={c.id} data-testid={`health-check-${c.id}`} data-status={c.status}
          className="grid grid-cols-[14px_240px_1fr_280px] items-baseline gap-x-2.5 border-b border-[color-mix(in_srgb,var(--color-border)_60%,transparent)] px-2.5 py-1.5">
          <span className="inline-block h-2 w-2 rounded-full"
            style={{ background: c.status === 'pass' ? KEPT : c.status === 'fail' ? ERR : DIM }} />
          <span className="text-[var(--color-text)]">{t(`cogHealth.check.${c.id}`)}</span>
          <span className={c.status === 'fail' ? 'text-[var(--color-status-error)]' : 'text-[var(--color-text-muted)]'}>
            {checkValue(t, c)}
          </span>
          <span className="text-right text-meta text-[var(--color-text-dim)]">{t(`cogHealth.requirement.${c.id}`, { req: c.requirement })}</span>
        </div>
      ))}
    </section>
  );
}

function CostSection({ data }: { data: CognitionHealth }) {
  const { t } = useTranslation();
  const { cost } = data;
  const top = Math.max(1e-9, ...cost.features.map((f) => f.usd));
  const dayMax = Math.max(1e-9, ...cost.days.map((d) => d.systemUsd));
  return (
    <section data-testid="health-cost">
      <SectionLabel>{t('cogHealth.q.cost')}</SectionLabel>
      <p className="mb-3 mt-1 text-meta text-[var(--color-text-dim)]">{t('cogHealth.cost.rule')}</p>
      {cost.features.map((f) => (
        <div key={f.feature} className="grid grid-cols-[240px_1fr_90px_64px] items-center gap-3 px-2.5 py-1.5">
          <span className="min-w-0">
            <span className="block text-[var(--color-text)]">{t(`cogHealth.feature.${f.feature.replace('.', '_')}`, { defaultValue: f.feature })}</span>
            <span className="block text-meta text-[var(--color-text-dim)]">
              {t('cogHealth.cost.calls', { calls: f.calls })}{f.avgUsd != null && ` · ${t('cogHealth.cost.avg', { usd: usd(f.avgUsd) })}`}
            </span>
          </span>
          <div><div className="h-2 rounded-sm" style={{ width: `${Math.max(0.5, (f.usd / top) * 100)}%`, background: WARN }} /></div>
          <span className="text-right tabular-nums text-[var(--color-text)]">{usd(f.usd)}</span>
          <span className="text-right text-meta tabular-nums text-[var(--color-text-dim)]">
            {cost.cdfUsd ? pct(f.usd / cost.cdfUsd) : ''}
          </span>
        </div>
      ))}
      {cost.unpricedCalls > 0 && (
        <p className="ml-2.5 text-meta text-[var(--color-text-dim)]">{t('cogHealth.cost.unpriced', { calls: cost.unpricedCalls })}</p>
      )}
      {cost.days.length > 0 && (
        <div className="ml-[262px] mr-2.5 mt-4">
          <div className="flex h-[90px] items-end gap-2.5">
            {cost.days.map((d) => (
              <span key={d.day} title={`${d.day} · ${usd(d.cdfUsd)} / ${usd(d.systemUsd)}`}
                className="flex flex-1 items-end rounded-t-sm"
                style={{ height: `${(d.systemUsd / dayMax) * 100}%`, background: 'color-mix(in srgb, var(--color-border) 90%, transparent)' }}>
                <i className="block w-full rounded-t-sm" style={{ height: d.systemUsd ? `${(d.cdfUsd / d.systemUsd) * 100}%` : 0, background: WARN }} />
              </span>
            ))}
          </div>
          <div className="mt-1 flex gap-2.5 text-meta text-[var(--color-text-dim)]">
            {cost.days.map((d) => <span key={d.day} className="flex-1 text-center">{d.day.slice(5)}</span>)}
          </div>
        </div>
      )}
    </section>
  );
}

function ReadingSection({ data, zh }: { data: CognitionHealth; zh: boolean }) {
  const { t, i18n } = useTranslation();
  const r = data.reading;
  const part = r ? (zh ? r.zh : r.en) : null;
  return (
    <section className="max-w-[900px]" data-testid="health-reading">
      <SectionLabel>{t('cogHealth.reading.title')}</SectionLabel>
      {!r || !part ? (
        <p className="mt-2 text-small text-[var(--color-text-dim)]">
          {data.readingFailed ? t('cogHealth.reading.failed') : t('cogHealth.reading.none')}
        </p>
      ) : (
        <>
          <div className="mb-3 mt-1 text-meta text-[var(--color-text-dim)]">
            {t('cogHealth.reading.by', { at: new Date(r.at).toLocaleString(i18n.language) })}
            {data.readingFailed && ` · ${t('cogHealth.reading.staleNote')}`}
          </div>
          {(['seen', 'why'] as const).map((k) => part[k].length > 0 && (
            <div key={k}>
              <h3 className="mb-1 mt-4 text-small font-semibold text-[var(--color-text)]">{t(`cogHealth.reading.${k}`)}</h3>
              {part[k].map((p, i) => <p key={i} className="mb-1.5 text-[var(--color-text)]">{p}</p>)}
            </div>
          ))}
          {part.suggest.length > 0 && (
            <>
              <h3 className="mb-1 mt-4 text-small font-semibold text-[var(--color-text)]">{t('cogHealth.reading.suggest')}</h3>
              <ol className="ml-[18px] list-decimal">
                {part.suggest.map((p, i) => <li key={i} className="my-0.5 text-[var(--color-text)]">{p}</li>)}
              </ol>
            </>
          )}
          <p className="mt-3.5 border-t border-[var(--color-border)] pt-2.5 text-meta text-[var(--color-text-muted)]">
            {t('cogHealth.reading.basis', { scored: r.basis.scored, checks: r.basis.checks })}{' '}
            {data.agreement.rate == null
              ? t('cogHealth.reading.noAgreement', { n: data.agreement.judgedByPerson })
              : t('cogHealth.reading.agreement', { pct: pct(data.agreement.rate), n: data.agreement.judgedByPerson })}
          </p>
        </>
      )}
    </section>
  );
}
