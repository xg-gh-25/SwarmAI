/**
 * Health — the cognition system's health inside the Need You overlay (design
 * docs/design/2026-10-31-cdf-health.md; layout Knowledge/Mockups/
 * 2026-11-24-needs-you-redesign.html). Read top to bottom:
 *
 * - one number: the share of what was kept that is useful, against its target, with the
 *   weekly rates beside it; under it three numbers: checks passed of those measured, what
 *   CDF cost (its share of all spend), the cost per useful entry;
 * - what to fix: every failing check and every destination below its target, each with
 *   "Hand to Swarm"; the judge's reading behind one disclosure;
 * - each destination as one split bar (useful / weak / withdraw) with its target as a
 *   tick; a row opens the sampled entries with the judge's reason, where the person can
 *   give their own verdict;
 * - the checks that passed as one line, opened on demand; the spend by purpose and by day.
 * When the checks last ran sits in the controls, amber once it is a day old.
 *
 * Data: GET /ingestion/cognition-health (backend core/ingestion/health.overview). The
 * judge only scores; nothing here removes an entry. "No data" is never shown as a pass.
 *
 * Key export: `CdfHealth`.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  ingestionService,
  type CognitionHealth,
  type HealthCheck,
  type HealthDestination,
  type HealthVerdict,
  type HealthWindow,
} from '../../services/ingestion';
import { Segmented } from '../overlay';
import { OverlayError, OverlayLoading } from './OverlayState';
import { Big, CARD, Compare, DIM, Kpis, MUTED, ZoneLabel, agoText, useGrown } from './needYouParts';
import type { DispatchPrompt } from './overlayRegistry';

const OK = 'var(--color-status-success)';
const WARN = 'var(--color-status-warning)';
const ERR = 'var(--color-status-error)';
const WEAK = 'color-mix(in srgb, var(--color-status-warning) 75%, transparent)';
const GROUPS: HealthDestination['group'][] = ['every_turn', 'on_demand', 'other'];
const MIN_SCORED = 10;
const STALE_MS = 24 * 3600 * 1000;

const pct = (v: number) => Math.round(v * 100);
const usd = (v: number) => (v >= 100 ? `$${Math.round(v)}` : v >= 1 ? `$${v.toFixed(1)}` : `$${v.toFixed(2)}`);
type T = (k: string, o?: Record<string, unknown>) => string;

function checkValue(t: T, c: HealthCheck): string {
  if (c.status === 'no_data' || !c.value) return t('cogHealth.noData');
  return t(`cogHealth.checkValue.${c.id}`, c.value as Record<string, unknown>);
}

export function CdfHealth({ onDispatch, close }: { onDispatch?: DispatchPrompt; close?: () => void }) {
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
  const hand = onDispatch ? (text: string) => { if (onDispatch(text, { autoSend: true })) close?.(); } : undefined;
  const data = q.data;
  const running = !!data?.running || recheck.isPending;
  const stale = !!data?.generatedAt && Date.now() - new Date(data.generatedAt).getTime() > STALE_MS;

  return (
    <div className="flex flex-col gap-8 py-2" data-testid="cdf-health">
      <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2">
        {data && (
          <span className="text-small" style={{ color: stale ? WARN : 'var(--color-text-muted)' }} data-testid="health-stamp" data-stale={stale}>
            {data.generatedAt ? (() => {
              const at = new Date(data.generatedAt).toLocaleString(i18n.language, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
              const ago = agoText(t, data.generatedAt);
              return ago ? t('cogHealth.stamp', { at, ago }) : t('cogHealth.stampAt', { at });
            })() : t('cogHealth.neverRun')}
          </span>
        )}
        <button type="button" data-testid="health-recheck" disabled={running} onClick={() => recheck.mutate()} className="btn-sm btn-secondary">
          {running ? t('cogHealth.running') : t('cogHealth.recheck')}
        </button>
        <Segmented<HealthWindow> value={window} onChange={setWindow}
          options={[{ key: '7d', label: t('cogHealth.w7d') }, { key: '30d', label: t('cogHealth.w30d') }]} />
      </div>
      {q.isPending && <OverlayLoading />}
      {(q.isError || (!q.isPending && !data)) && (
        <OverlayError testid="health-load-error" retryTestid="health-load-retry"
          message={t('cogHealth.loadFailed')} onRetry={() => { void q.refetch(); }} />
      )}
      {data && (
        <>
          <Verdict data={data} window={window} />
          <Fixes data={data} zh={zh} onHand={hand} />
          <Destinations data={data} zh={zh} />
          <Checks checks={data.checks} />
          <Cost data={data} />
        </>
      )}
    </div>
  );
}

function Verdict({ data, window }: { data: CognitionHealth; window: HealthWindow }) {
  const { t } = useTranslation();
  const measured = data.checks.filter((c) => c.status !== 'no_data');
  const passed = measured.filter((c) => c.status === 'pass').length;
  const { rate, target } = data.value;
  const gap = rate == null ? null : pct(target) - Math.round(rate * 1000) / 10;
  const trend = data.value.trend;
  return (
    <section className={`${CARD} px-[18px] py-4`} data-testid="health-verdict">
      <div className="flex items-start gap-4">
        <div className="min-w-0 flex-1">
          {rate == null ? (
            <p className={`text-heading ${MUTED}`} data-testid="health-line">{t('cogHealth.line.notEnough')}</p>
          ) : (
            <Big testid="health-rate" value={(Math.round(rate * 1000) / 10).toString()} unit="%" what={t('cogHealth.usefulWhat')}
              compare={<Compare testid="health-target"
                change={gap != null && gap > 0 ? t('cogHealth.below', { pts: gap.toFixed(1).replace(/\.0$/, '') }) : t('cogHealth.onTarget')}
                good={!(gap != null && gap > 0)} was={t('cogHealth.target', { pct: pct(target) })} />} />
          )}
        </div>
        {trend.length > 1 && (
          <div className="flex h-[34px] items-end gap-1" role="img" data-testid="health-weeks"
            aria-label={trend.map((w) => `${w.week} ${w.rate == null ? '—' : `${pct(w.rate)}%`}`).join(', ')}>
            {trend.map((w) => (
              <i key={w.week} className="block w-3 rounded-t-sm" title={`${w.week} · ${w.rate == null ? '—' : `${pct(w.rate)}%`}`}
                style={{ height: w.rate == null ? 2 : Math.max(4, ((w.rate - 0.7) / 0.3) * 34),
                  background: w.rate != null && w.rate < target ? WEAK : `color-mix(in srgb, ${OK} 65%, transparent)` }} />
            ))}
          </div>
        )}
      </div>
      <Kpis testid="health-kpis" items={[
        { key: 'checks', label: t('cogHealth.q.checks'), warn: passed < measured.length,
          value: measured.length ? <>{passed}<small className={`mx-0.5 text-small font-medium ${MUTED}`}>/</small>{measured.length}<small className={`ml-1 text-small font-medium ${MUTED}`}>{t('cogHealth.passed')}</small></> : '—' },
        { key: 'cost', label: t(`cogHealth.costIn${window}`),
          value: <>{usd(data.cost.cdfUsd)}{data.cost.share != null && <small className={`ml-1 text-small font-medium ${MUTED}`}>{t('cogHealth.costShare', { pct: pct(data.cost.share) })}</small>}</> },
        { key: 'perUseful', label: t('cogHealth.perUsefulLabel'), value: data.cost.perUsefulUsd == null ? '—' : usd(data.cost.perUsefulUsd) },
      ]} />
    </section>
  );
}

function Fixes({ data, zh, onHand }: { data: CognitionHealth; zh: boolean; onHand?: (text: string) => void }) {
  const { t } = useTranslation();
  const [readOpen, setReadOpen] = useState(false);
  const failing = data.checks.filter((c) => c.status === 'fail');
  const low = data.value.destinations.filter((d) => d.rate != null && d.scored >= MIN_SCORED && d.rate < d.target)
    .sort((a, b) => (a.rate ?? 0) - (b.rate ?? 0));
  const part = data.reading ? (zh ? data.reading.zh : data.reading.en) : null;
  const rows = [
    ...failing.map((c) => ({ key: `check-${c.id}`, title: t(`cogHealth.check.${c.id}`), num: '', tag: t(`cogHealth.requirement.${c.id}`, { req: c.requirement }),
      line: checkValue(t, c) })),
    ...low.map((d) => ({ key: `dest-${d.key}`, title: t(`cogHealth.dest.${d.key}`), num: `${pct(d.rate ?? 0)}%`, tag: t('cogHealth.target', { pct: pct(d.target) }),
      line: t('cogHealth.lowLine', { scored: d.scored, weak: d.weak, withdraw: d.withdraw }) })),
  ];
  return (
    <section data-testid="health-fixes">
      <ZoneLabel count={rows.length}>{t('cogHealth.fixes')}</ZoneLabel>
      {rows.length === 0 ? (
        <p className={`text-body ${MUTED}`} data-testid="health-fixes-none">{t('cogHealth.fixesNone')}</p>
      ) : (
        <div className="flex flex-col divide-y divide-[var(--color-border)]">
          {rows.map((r) => (
            <div key={r.key} className="grid grid-cols-[18px_minmax(0,1fr)_auto] gap-x-3 py-3" data-testid={`health-fix-${r.key}`}>
              <span aria-hidden className="grid h-[18px] w-[18px] place-items-center rounded-full text-meta font-semibold"
                style={{ color: WARN, background: 'color-mix(in srgb, var(--color-status-warning) 12%, transparent)' }}>!</span>
              <div className="min-w-0">
                <div className="text-body">
                  <b className="font-semibold">{r.title}</b>
                  {r.num && <b className="ml-1.5 font-semibold tabular-nums" style={{ color: WARN }}>{r.num}</b>}
                  <span className={`ml-1.5 text-small ${MUTED}`}>{r.tag}</span>
                </div>
                <div className="mt-0.5 text-small text-[var(--color-text-secondary)]">{r.line}</div>
              </div>
              {onHand && (
                <button type="button" className="btn-sm btn-secondary self-start" style={{ color: 'var(--color-zone-cognitive)' }}
                  onClick={() => onHand(t('cogHealth.fixPrompt', { what: `${r.title} ${r.num}`.trim(), detail: `${r.line} (${r.tag})`,
                    suggest: part?.suggest.length ? part.suggest.map((s, i) => `${i + 1}. ${s}`).join('\n') : '—' }))}>
                  {t('needYou.hand')}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      {part && (
        <>
          <button type="button" className={`mt-1 text-small ${MUTED} hover:text-[var(--color-text)]`} aria-expanded={readOpen}
            onClick={() => setReadOpen((v) => !v)} data-testid="health-reading-toggle">
            {t('cogHealth.reading.title')} {readOpen ? '▾' : '▸'}
          </button>
          {readOpen && data.reading && (
            <div className="mt-2 max-w-[860px] space-y-1.5 text-small text-[var(--color-text-secondary)]" data-testid="health-reading">
              {[...part.seen, ...part.why].map((p, i) => <p key={i}>{p}</p>)}
              {part.suggest.length > 0 && (
                <ol className="ml-[18px] list-decimal">{part.suggest.map((p, i) => <li key={i} className="my-0.5">{p}</li>)}</ol>
              )}
              <p className={`pt-1 text-meta ${DIM}`}>
                {t('cogHealth.reading.basis', { scored: data.reading.basis.scored, checks: data.reading.basis.checks })}{' '}
                {data.agreement.rate == null
                  ? t('cogHealth.reading.noAgreement', { n: data.agreement.judgedByPerson })
                  : t('cogHealth.reading.agreement', { pct: pct(data.agreement.rate), n: data.agreement.judgedByPerson })}
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Destinations({ data, zh }: { data: CognitionHealth; zh: boolean }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const grown = useGrown(data.value.destinations.map((d) => `${d.key}:${d.useful}/${d.weak}/${d.withdraw}`).join('|'));
  return (
    <section data-testid="health-value">
      <ZoneLabel right={(
        <span className={`flex gap-3.5 text-small ${MUTED}`}>
          {(['useful', 'weak', 'withdraw'] as const).map((v) => (
            <span key={v} className="inline-flex items-center gap-1.5"><i className="inline-block h-2 w-2 rounded-sm"
              style={{ background: v === 'useful' ? OK : v === 'weak' ? WEAK : ERR }} />{t(`cogHealth.verdict.${v}`)}</span>
          ))}
        </span>
      )}>{t('cogHealth.destinations')}</ZoneLabel>
      {GROUPS.map((group) => {
        const ds = data.value.destinations.filter((d) => d.group === group);
        if (!ds.length) return null;
        return (
          <div key={group} className="mb-2">
            <div className={`pb-1 pt-2 text-meta font-semibold tracking-[0.04em] ${DIM}`}>{t(`cogHealth.group.${group}`)}</div>
            {ds.map((d) => {
              const few = d.rate == null || d.scored < MIN_SCORED;
              const total = d.useful + d.weak + d.withdraw || 1;
              const low = !few && (d.rate ?? 0) < d.target;
              return (
                <div key={d.key}>
                  <button type="button" data-testid={`health-dest-${d.key}`} aria-expanded={open === d.key}
                    onClick={() => setOpen(open === d.key ? null : d.key)}
                    className="grid w-full grid-cols-[minmax(104px,1fr)_minmax(72px,2.2fr)_64px_72px] items-center gap-x-3.5 rounded-md border-t border-[var(--color-border)] px-1.5 py-[7px] text-left text-body hover:bg-[var(--color-hover)]">
                    <span className="truncate font-semibold">{t(`cogHealth.dest.${d.key}`)}</span>
                    <span className="relative" style={{ opacity: few ? 0.35 : 1 }}>
                      <span className="flex h-2 overflow-hidden rounded bg-[var(--color-bg-chrome)]">
                        {([['useful', OK], ['weak', WEAK], ['withdraw', ERR]] as const).map(([k, c]) => (
                          <i key={k} className="so-width block h-full" style={{ width: grown ? `${(d[k] / total) * 100}%` : '0%', background: c }} />
                        ))}
                      </span>
                      <span aria-hidden className="absolute -bottom-[3px] -top-[3px] w-0.5 rounded-[1px] opacity-55"
                        style={{ left: `${d.target * 100}%`, background: 'var(--color-text)' }} />
                    </span>
                    <span className={`text-right tabular-nums ${few ? `text-small ${DIM}` : 'font-semibold'}`} style={low ? { color: WARN } : undefined}>
                      {few ? t('cogHealth.fewSamples') : `${pct(d.rate ?? 0)}%`}
                    </span>
                    <span className={`whitespace-nowrap text-right text-small tabular-nums ${DIM}`}>{t('cogHealth.scored', { n: d.scored })}</span>
                  </button>
                  {open === d.key && <Samples d={d} zh={zh} />}
                </div>
              );
            })}
          </div>
        );
      })}
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
    return <div className={`mb-2 ml-1 border-l-2 border-[var(--color-border)] px-3 py-2 text-small ${DIM}`}>{t('cogHealth.value.noSamples')}</div>;
  }
  return (
    <div className="mb-2 ml-1 border-l-2 border-[var(--color-border)] py-1 pl-3" data-testid={`health-samples-${d.key}`}>
      {d.samples.map((s) => {
        const shown = s.userVerdict ?? s.verdict;
        const tone = shown === 'useful' ? OK : shown === 'weak' ? WARN : ERR;
        return (
          <div key={s.unitId} className="grid grid-cols-[44px_minmax(0,1fr)_auto] items-start gap-x-2.5 py-1.5 text-small">
            <span className="h-[18px] rounded text-center text-meta font-semibold leading-[18px]"
              style={{ color: tone, background: `color-mix(in srgb, ${tone} 12%, transparent)` }}
              title={s.userVerdict ? t('cogHealth.value.judgeSaid', { verdict: t(`cogHealth.verdict.${s.verdict}`) }) : undefined}>
              {t(`cogHealth.verdict.${shown}`)}
            </span>
            <span className="min-w-0">
              <span className="block font-semibold text-[var(--color-text)]">{s.title}{s.project && <span className={`font-normal ${DIM}`}> · {s.project}</span>}</span>
              <span className={`block ${MUTED}`}>
                {s.klass && s.verdict === 'withdraw' && <>{t(`cogHealth.klass.${s.klass}`)} · </>}{zh ? s.reasonZh || s.reasonEn : s.reasonEn || s.reasonZh}
              </span>
            </span>
            <span className="flex gap-1">
              {(['useful', 'weak', 'withdraw'] as const).map((v) => (
                <button key={v} type="button" data-testid={`health-verdict-${s.unitId}-${v}`} disabled={set.isPending}
                  onClick={() => set.mutate({ unitId: s.unitId, verdict: v })} aria-pressed={s.userVerdict === v}
                  className="btn-sm btn-secondary aria-pressed:bg-[var(--color-text)] aria-pressed:text-[var(--color-bg)]">
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

function Checks({ checks }: { checks: HealthCheck[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const rest = checks.filter((c) => c.status !== 'fail');
  const passed = rest.filter((c) => c.status === 'pass').length;
  if (!checks.length) return <section data-testid="health-checks"><ZoneLabel>{t('cogHealth.q.checks')}</ZoneLabel><p className={`text-small ${DIM}`}>{t('cogHealth.neverRun')}</p></section>;
  return (
    <section data-testid="health-checks">
      <ZoneLabel>{t('cogHealth.q.checks')}</ZoneLabel>
      <div className="flex items-center gap-2.5 text-body">
        {/* green only when every one of them passed: a check with no data is no pass */}
        <span className="inline-block h-2 w-2 rounded-full" data-testid="health-checks-dot"
          style={{ background: passed > 0 && passed === rest.length ? OK : 'var(--color-text-dim)' }} />
        <span>{t('cogHealth.restPassed', { count: passed })}{rest.length > passed && ` · ${t('cogHealth.restNoData', { count: rest.length - passed })}`}</span>
        <button type="button" className={`text-small ${MUTED} hover:text-[var(--color-text)]`} aria-expanded={open}
          onClick={() => setOpen((v) => !v)} data-testid="health-checks-toggle">{open ? t('cogHealth.collapse') : t('cogHealth.expand')}</button>
      </div>
      {open && rest.map((c) => (
        <div key={c.id} data-testid={`health-check-${c.id}`} data-status={c.status}
          className="mt-1 grid grid-cols-[14px_minmax(0,1.2fr)_minmax(0,1.6fr)] gap-x-3 border-t border-[var(--color-border)] py-1.5 text-small">
          <span className="mt-[5px] inline-block h-2 w-2 rounded-full" style={{ background: c.status === 'pass' ? OK : 'var(--color-text-dim)' }} />
          <span>{t(`cogHealth.check.${c.id}`)}</span>
          <span className={`tabular-nums ${MUTED}`}>{checkValue(t, c)}</span>
        </div>
      ))}
    </section>
  );
}

function Cost({ data }: { data: CognitionHealth }) {
  const { t } = useTranslation();
  const { cost } = data;
  const features = cost.features.filter((f) => f.usd >= 0.05);
  const top = Math.max(1e-9, ...features.map((f) => f.usd));
  const dayMax = Math.max(1e-9, ...cost.days.map((d) => d.systemUsd));
  const grown = useGrown(features.map((f) => `${f.feature}:${f.usd}`).join('|'));
  return (
    <section data-testid="health-cost">
      <ZoneLabel>{t('cogHealth.q.cost')}</ZoneLabel>
      <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div>
          {features.map((f) => (
            <div key={f.feature} className="grid grid-cols-[128px_minmax(0,1fr)_56px] items-center gap-x-2.5 py-1 text-small"
              title={t('cogHealth.cost.calls', { calls: f.calls }) + (f.avgUsd != null ? ` · ${t('cogHealth.cost.avg', { usd: usd(f.avgUsd) })}` : '')}>
              <span className="truncate">{t(`cogHealth.feature.${f.feature.replace('.', '_')}`, { defaultValue: f.feature })}</span>
              <span className="h-1.5 overflow-hidden rounded-[3px] bg-[var(--color-bg-chrome)]">
                <span className="so-width block h-full" style={{ width: grown ? `${(f.usd / top) * 100}%` : '0%', background: WARN }} /></span>
              <span className="text-right tabular-nums text-[var(--color-text-secondary)]">{usd(f.usd)}</span>
            </div>
          ))}
          {cost.unpricedCalls > 0 && <p className={`mt-1 text-meta ${DIM}`}>{t('cogHealth.cost.unpriced', { calls: cost.unpricedCalls })}</p>}
        </div>
        {cost.days.length > 0 && (
          <div>
            <div className="flex h-[70px] items-end gap-[3px] border-b border-[var(--color-border)]">
              {cost.days.map((d) => (
                <span key={d.day} className="flex h-full flex-1 flex-col-reverse" title={t('cogHealth.cost.dayTip', { day: d.day.slice(5), cdf: usd(d.cdfUsd), all: usd(d.systemUsd) })}>
                  <i className="block" style={{ height: `${(d.cdfUsd / dayMax) * 100}%`, background: WARN }} />
                  <i className="block rounded-t-sm" style={{ height: `${(Math.max(0, d.systemUsd - d.cdfUsd) / dayMax) * 100}%`,
                    background: 'color-mix(in srgb, var(--color-text-dim) 35%, transparent)' }} />
                </span>
              ))}
            </div>
            <div className={`mt-1 flex justify-between text-meta ${DIM}`}>
              <span>{cost.days[0].day.slice(5).replace('-', '/')}</span>
              <span>{t('cogHealth.cost.legend')}</span>
              <span>{cost.days[cost.days.length - 1].day.slice(5).replace('-', '/')}</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
