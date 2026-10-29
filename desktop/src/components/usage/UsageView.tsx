/**
 * UsageView — where the platform's model spend goes (design
 * docs/design/2026-10-29-usage-accounting.md §5, mockup
 * Knowledge/Designs/mockups/2026-10-30-usage-view.html).
 *
 * Top down it answers: how much this window spent and which way it moves against the
 * window before (the one number that stands out); one sentence on where the money concentrates; the daily spend; where
 * it went (by surface, feature, model or machine); the largest sessions, pipeline runs,
 * jobs and Hive runs, each row opening what it names; what a turn costs; and what the
 * ledger cannot see. Every figure comes from GET /api/usage/report (services/usage).
 *
 * A cost is the CLI's book cost. Tokens with no known price show "cost unknown", never
 * $0. The pipeline row is a split of chat spend, shown apart and never added in.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { chatService } from '../../services/chat';
import { useOverlay } from '../../contexts/OverlayContext';
import {
  useUsageReport,
  type UsageDimension, type UsageGroup, type UsageReport, type UsageTop, type UsageWindow,
} from '../../services/usage';
import { change, money, pct, rowCost, SURFACE_COLOR, tokens } from './format';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const WINDOWS: { id: UsageWindow; key: string }[] = [{ id: '7d', key: 'd7' }, { id: '30d', key: 'd30' }, { id: 'all', key: 'all' }];
const DIMENSIONS: UsageDimension[] = ['surface', 'feature', 'model', 'machine'];
const TOPS: UsageTop[] = ['sessions', 'runs', 'jobs', 'hive'];

export interface UsageViewProps {
  close?: () => void;
  resumeSession?: (session: unknown) => boolean;
}

function Seg<T extends string>({ value, options, onChange, label, testid }: {
  value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; label: string; testid: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="inline-flex rounded-lg bg-[var(--color-bg)] p-0.5" data-testid={testid}>
      {options.map((o) => (
        <button key={o.id} type="button" role="tab" aria-selected={o.id === value} onClick={() => onChange(o.id)}
          className={`rounded-md px-3 py-0.5 text-meta ${o.id === value ? 'bg-[var(--color-card)] text-[var(--color-text)] font-medium' : muted}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function UsageView({ close, resumeSession }: UsageViewProps) {
  const { t } = useTranslation();
  const [window, setWindow] = useState<UsageWindow>('7d');
  const { data, isLoading, isError } = useUsageReport(window);

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="usage-view">
      <div className="flex shrink-0 items-center gap-3 border-b border-[var(--color-border)] px-6 py-3">
        <Seg value={window} onChange={setWindow} label={t('usage.windowLabel')} testid="usage-window"
          options={WINDOWS.map((w) => ({ id: w.id, label: t(`usage.window.${w.key}`) }))} />
        {data && (
          <span className={`ml-auto text-meta ${muted}`} data-testid="usage-scope">
            {data.gaps.hives_reporting.length
              ? t('usage.scopeHives', { count: data.gaps.hives_reporting.length })
              : t('usage.scopeLocal')}
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 pt-5">
        {isLoading && <p className={muted}>{t('usage.loading')}</p>}
        {isError && <p className="text-[var(--color-status-error)]">{t('usage.error')}</p>}
        {data && <Report r={data} close={close} resumeSession={resumeSession} />}
      </div>
    </div>
  );
}

function Report({ r, close, resumeSession }: { r: UsageReport } & UsageViewProps) {
  const { t } = useTranslation();
  if (r.total.rows === 0) return <p className={muted} data-testid="usage-empty">{t('usage.empty')}</p>;
  return (
    <>
      <Hero r={r} />
      <Insight r={r} />
      <Bars r={r} />
      <Where r={r} />
      <div className="mt-2 grid grid-cols-1 gap-6 lg:grid-cols-[1.35fr_1fr]">
        <Top r={r} close={close} resumeSession={resumeSession} />
        <Turns r={r} />
      </div>
      <Gaps r={r} />
    </>
  );
}

function Hero({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  const c = change(r.total.cost_usd, r.previous?.cost_usd);
  const cost = rowCost(r.total);                     // null: tokens, none of them priced
  return (
    <section data-testid="usage-hero">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-[30px] font-bold tabular-nums" data-testid="usage-total">{cost ?? t('usage.costUnknown')}</span>
        {r.previous && (c
          ? <span className="text-body tabular-nums" data-testid="usage-change"
              style={{ color: c.dir === 'up' ? 'var(--color-status-warning)' : 'var(--color-status-success)' }}>
              {t(c.dir === 'up' ? 'usage.vsPrevUp' : 'usage.vsPrevDown', { pct: c.pct, prev: money(r.previous.cost_usd) })}
            </span>
          : <span className={`text-body ${muted}`}>{t('usage.vsPrevNone')}</span>)}
        {cost != null && <span className={muted}>· {t('usage.perDay', { v: money(r.total.cost_usd / Math.max(1, r.days)) })}</span>}
      </div>
      <div className={`mt-2 flex flex-wrap gap-x-7 gap-y-1 text-meta ${muted}`}>
        <Fact v={tokens(r.total.billed)} label={t('usage.facts.billed')} />
        <Fact v={pct(r.total.cache_hit_rate)} label={t('usage.facts.hit')} />
        {r.turns.p50 != null && <Fact v={money(r.turns.p50)} label={t('usage.facts.p50')} />}
        {r.turns.p90 != null && <Fact v={money(r.turns.p90)} label={t('usage.facts.p90')} />}
        {r.turns.max != null && <Fact v={money(r.turns.max)} label={t('usage.facts.max')} />}
      </div>
    </section>
  );
}

function Fact({ v, label }: { v: string; label: string }) {
  return <span><b className="mr-1 text-body font-semibold text-[var(--color-text)] tabular-nums">{v}</b>{label}</span>;
}

function Insight({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  const c = r.concentration;
  const parts: string[] = [];
  if (c.top_sessions_share != null && c.top_sessions > 1) parts.push(t('usage.insight.top', { n: c.top_sessions, share: Math.round(c.top_sessions_share * 100) }));
  if (c.big_turns > 0 && c.big_turns_cost_share != null && c.big_turns_turn_share != null) {
    parts.push(t('usage.insight.big', { usd: money(c.big_turn_usd), turns: c.big_turns,
      turnShare: Math.round(c.big_turns_turn_share * 100), costShare: Math.round(c.big_turns_cost_share * 100) }));
  }
  if (!parts.length) return null;
  return (
    <div className="mt-4 rounded-lg border-l-[3px] px-3.5 py-2.5 text-[var(--color-text-secondary)]" data-testid="usage-insight"
      style={{ borderColor: 'var(--color-status-warning)', background: 'color-mix(in srgb, var(--color-status-warning) 9%, transparent)' }}>
      <b className="text-[var(--color-text)]">{parts.join(t('usage.insight.sep'))}</b> {t('usage.insight.hint')}
    </div>
  );
}

function Bars({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  const max = Math.max(...r.daily.map((d) => d.cost_usd), 0);
  if (max <= 0) return null;
  const peak = r.daily.reduce((a, d) => (d.cost_usd > a.cost_usd ? d : a), r.daily[0]);
  const last = r.daily[r.daily.length - 1];
  const surfaces = [...new Set(r.daily.flatMap((d) => Object.keys(d.by_surface)))];
  return (
    <section className="mt-5" data-testid="usage-bars">
      <div className="flex h-[110px] items-end gap-[3px] border-b border-[var(--color-border)] pt-4">
        {r.daily.map((d) => (
          <div key={d.day} className="relative flex h-full flex-1 flex-col justify-end" title={`${d.day} ${money(d.cost_usd)}`}
            style={{ opacity: d.in_window ? 1 : 0.45 }} data-testid="usage-bar">
            {(d === peak || d === last) && d.cost_usd > 0 && (
              <span className={`absolute -top-4 left-1/2 -translate-x-1/2 whitespace-nowrap text-[10px] ${dim}`}>{money(d.cost_usd)}</span>
            )}
            {Object.entries(d.by_surface).map(([s, v], i, arr) => (
              <i key={s} className="block" style={{ height: `${(v / max) * 100}%`, background: SURFACE_COLOR[s] ?? '#7d8590',
                borderRadius: i === arr.length - 1 ? '3px 3px 0 0' : undefined }} />
            ))}
          </div>
        ))}
      </div>
      <div className={`mt-1 flex justify-between text-[10.5px] ${dim}`}>
        <span>{r.daily[0].day.slice(5)}</span><span>{t('usage.axis')}</span><span>{last.day.slice(5)}</span>
      </div>
      {surfaces.length > 1 && (
        <div className={`mt-1.5 flex gap-3 text-[11px] ${muted}`}>
          {surfaces.map((s) => (
            <span key={s} className="inline-flex items-center gap-1">
              <i className="inline-block h-2 w-2 rounded-sm" style={{ background: SURFACE_COLOR[s] ?? '#7d8590' }} />{s}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function Where({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  const [d, setD] = useState<UsageDimension>('surface');
  const rows = r.breakdown[d];
  const total = r.total.cost_usd;
  const a = r.attribution;
  return (
    <section data-testid="usage-where">
      <h3 className="mb-2 mt-7 flex items-center gap-2.5 text-body font-semibold">
        {t('usage.where.title')}
        <Seg value={d} onChange={setD} label={t('usage.where.title')} testid="usage-dimension"
          options={DIMENSIONS.map((x) => ({ id: x, label: t(`usage.where.${x}`) }))} />
      </h3>
      <table className="w-full border-collapse text-meta">
        <thead>
          <tr className={`text-left text-[11px] ${dim}`}>
            <th className="px-2 pb-1.5 font-medium">{t(`usage.dimName.${d}`)}</th>
            <th className="px-2 pb-1.5 font-medium">{t('usage.col.share')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.cost')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.billed')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.hit')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.calls')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.perCall')}</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((g) => {
            const cost = rowCost(g);
            const share = total > 0 && cost != null ? g.cost_usd / total : null;
            return (
              <tr key={g.key ?? '—'} className="border-t border-[var(--color-border)]" data-testid="usage-where-row">
                <td className="px-2 py-1.5">{g.key ?? t('usage.none')}</td>
                <td className="px-2 py-1.5">
                  {share != null && share > 0
                    ? <span className="flex items-center gap-2"><span className="h-1.5 rounded bg-[var(--color-zone-system)]" style={{ width: `${Math.max(2, share * 180)}px` }} />{pct(share)}</span>
                    : <span className={dim}>—</span>}
                </td>
                <td className={`px-2 py-1.5 text-right ${cost == null ? dim : ''}`}>{cost ?? t('usage.costUnknown')}</td>
                <td className="px-2 py-1.5 text-right">{tokens(g.billed)}</td>
                <td className="px-2 py-1.5 text-right">{pct(g.cache_hit_rate)}</td>
                <td className="px-2 py-1.5 text-right">{g.calls}</td>
                <td className="px-2 py-1.5 text-right">{cost != null && g.calls ? money(g.cost_usd / g.calls) : ''}</td>
              </tr>
            );
          })}
          {d === 'surface' && a.runs > 0 && (
            <tr className={`border-t border-[var(--color-border)] ${muted}`} data-testid="usage-attribution">
              <td className="px-2 py-1.5" colSpan={2}>{t('usage.attribution', { count: a.runs })}</td>
              <td className="px-2 py-1.5 text-right">{rowCost(a) ?? t('usage.costUnknown')}</td>
              <td className="px-2 py-1.5 text-right">{tokens(a.billed)}</td>
              <td className="px-2 py-1.5 text-right">{pct(a.cache_hit_rate)}</td>
              <td colSpan={2} />
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function Top({ r, close, resumeSession }: { r: UsageReport } & UsageViewProps) {
  const { t } = useTranslation();
  const { openOverlay } = useOverlay();
  const [which, setWhich] = useState<UsageTop>('sessions');
  const [notice, setNotice] = useState<string | null>(null);
  const rows = r.top[which];
  const open = (g: UsageGroup): (() => void) | null => {
    if (which === 'sessions' && g.key && resumeSession) {
      const id = g.key;
      return () => void (async () => {
        try {
          const s = await chatService.getSession(id);
          if (s && resumeSession(s)) { close?.(); return; }
        } catch { /* deleted or unknown */ }
        setNotice(t('usage.top.gone'));
      })();
    }
    if (which === 'runs' && g.report_path) {
      const path = g.report_path;
      return () => { close?.(); document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path } })); };
    }
    if (which === 'jobs') return () => openOverlay('jobs');
    if (which === 'hive') return () => openOverlay('hive');
    return null;
  };
  const label = (g: UsageGroup) => g.title || (which === 'hive' ? g.machine : null) || g.key || t('usage.none');
  return (
    <section data-testid="usage-top">
      <h3 className="mb-2 mt-7 flex items-center gap-2.5 text-body font-semibold">
        {t('usage.top.title')}
        <Seg value={which} onChange={(v) => { setWhich(v); setNotice(null); }} label={t('usage.top.title')} testid="usage-top-kind"
          options={TOPS.map((x) => ({ id: x, label: t(`usage.top.${x}`) }))} />
      </h3>
      {rows.length === 0 ? <p className={`text-meta ${muted}`}>{t('usage.top.empty')}</p> : (
        <table className="w-full border-collapse text-meta">
          <thead>
            <tr className={`text-left text-[11px] ${dim}`}>
              <th className="px-2 pb-1.5 font-medium">{t(`usage.top.${which}`)}</th>
              <th className="px-2 pb-1.5 text-right font-medium">{t('usage.col.cost')}</th>
              <th className="px-2 pb-1.5 text-right font-medium">{t(which === 'sessions' ? 'usage.col.turns' : 'usage.col.calls')}</th>
              <th className="px-2 pb-1.5 text-right font-medium">{t(which === 'sessions' ? 'usage.col.perTurn' : 'usage.col.perCall')}</th>
              <th />
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map((g) => {
              const go = open(g);
              const cost = rowCost(g);
              return (
                <tr key={g.key ?? ''} className="border-t border-[var(--color-border)]" data-testid="usage-top-row">
                  <td className="max-w-0 truncate px-2 py-1.5" title={label(g) ?? undefined}>{label(g)}</td>
                  <td className="px-2 py-1.5 text-right">{cost ?? t('usage.costUnknown')}</td>
                  <td className="px-2 py-1.5 text-right">{g.calls}</td>
                  <td className="px-2 py-1.5 text-right">{cost != null && g.calls ? money(g.cost_usd / g.calls) : ''}</td>
                  <td className="w-6 px-1 text-right">
                    {go && <button type="button" onClick={go} title={t('usage.top.open')} aria-label={t('usage.top.open')}
                      className={`${muted} hover:text-[var(--color-text)]`} data-testid="usage-top-open">↗</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {notice && <p className="mt-1 text-meta text-[var(--color-status-error)]" role="alert">{notice}</p>}
    </section>
  );
}

function Turns({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  if (!r.turns.turns) return null;
  const range = (b: UsageReport['turns']['buckets'][number]) => (b.to == null ? `≥ ${money(b.from)}` : b.from === 0 ? `< ${money(b.to)}` : `${money(b.from)} – ${money(b.to)}`);
  return (
    <section data-testid="usage-turns">
      <h3 className="mb-2 mt-7 text-body font-semibold">{t('usage.turns.title')}</h3>
      <table className="w-full border-collapse text-meta">
        <thead>
          <tr className={`text-left text-[11px] ${dim}`}>
            <th className="px-2 pb-1.5 font-medium">{t('usage.turns.range')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.turns.turns')}</th>
            <th className="px-2 pb-1.5 text-right font-medium">{t('usage.turns.share')}</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {r.turns.buckets.map((b) => (
            <tr key={b.from} className="border-t border-[var(--color-border)]">
              <td className="px-2 py-1.5">{range(b)}</td>
              <td className="px-2 py-1.5 text-right">{b.turns}</td>
              <td className="px-2 py-1.5 text-right">{pct(b.share)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={`mt-2.5 text-meta ${muted}`}>{t('usage.turns.note')}</p>
    </section>
  );
}

function Gaps({ r }: { r: UsageReport }) {
  const { t } = useTranslation();
  const g = r.gaps;
  const items = [
    ...g.unpriced.map((u) => t('usage.gaps.unpriced', { surface: u.surface, calls: u.calls, tokens: tokens(u.tokens) })),
    ...(g.imported_cost_usd > 0 ? [t('usage.gaps.imported', { cost: money(g.imported_cost_usd) })] : []),
    ...(g.live_since ? [t('usage.gaps.liveSince', { date: g.live_since.slice(0, 10) })] : []),
    ...(g.hives_reporting.length ? [] : [t('usage.gaps.noHive')]),
  ];
  if (!items.length) return null;
  return (
    <section className={`mt-6 rounded-lg border border-dashed border-[var(--color-border)] px-3.5 py-2.5 text-meta ${muted}`} data-testid="usage-gaps">
      <b className="text-[var(--color-text-secondary)]">{t('usage.gaps.title')}</b>
      <ul className="mt-1 list-disc pl-5">{items.map((s) => <li key={s}>{s}</li>)}</ul>
    </section>
  );
}

export default UsageView;
