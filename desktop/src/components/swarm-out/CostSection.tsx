/**
 * CostSection — the history's cost (mock Knowledge/Mockups/2026-11-23-swarm-out-dashboard-
 * redesign-v7.html, the "花费" box), under the verdict, for the history's time range.
 *
 * Two layers on one table (./costStats): "with machine" (default; which carrier is
 * cheaper) and "model only" (whether a kind of work costs more, or retries more, on one
 * carrier). One verdict at the top: the cheaper carrier per accepted run over the kinds
 * both have enough accepted runs for, or "cannot compare yet". Below it the totals and,
 * with machine, the EC2 Hives' busy share; then one block per kind of work, a row per
 * carrier with a three-part bar (model, machine in use, EC2 idle) and dollars per accepted
 * run. A block opens to its tokens per accepted run. Machine cost is an estimate, and runs
 * whose machine cost is unknown are counted, never guessed.
 */
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SwarmOutMachines, SwarmOutRun } from '../../services/swarmOut';
import {
  CARRIERS, comparable, costStats, MIN_ACCEPTED, perAccepted, verdict,
  type CostLayer, type KindRow, type Tokens,
} from './costStats';
import { Kind } from './SwarmOutSettings';
import { stamp, type Carrier } from './where';

const muted = 'text-[var(--color-text-muted)]';
const dim = 'text-[var(--color-text-dim)]';
const card = 'so-card rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const SEG = {
  model: 'color-mix(in srgb, var(--color-primary) 75%, transparent)',
  busy: 'var(--color-zone-work)',
  idle: 'repeating-linear-gradient(135deg, color-mix(in srgb, var(--color-text-dim) 75%, transparent) 0 3px, color-mix(in srgb, var(--color-text-dim) 25%, transparent) 3px 6px)',
};

export const money = (v: number): string =>
  v >= 100 ? `$${Math.round(v).toLocaleString('en-US')}` : v >= 10 ? `$${v.toFixed(1)}` : `$${v.toFixed(2)}`;
const tok = (v: number): string => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : String(Math.round(v)));
const hit = (t: Tokens): number => t.cache_read / Math.max(1, t.cache_read + t.input + t.cache_write);

export function CostSection({ runs, machines, from, to, grown, compact }: {
  runs: SwarmOutRun[]; machines: SwarmOutMachines | null | undefined; from: number; to: number; grown: boolean; compact: boolean;
}) {
  const { t } = useTranslation();
  const [layer, setLayer] = useState<CostLayer>('all');
  const [open, setOpen] = useState<Set<string>>(new Set());
  const view = useMemo(() => costStats(runs, machines, from, to, layer), [runs, machines, from, to, layer]);
  const v = verdict(view);
  const acRuns = runs.filter((r) => (r.rounds ?? []).some((x) => x?.carrier === 'agentcore')).length;
  let max = 0;
  for (const k of view.kinds) for (const c of CARRIERS) {
    const p = perAccepted(k.sides[c]);
    if (p && (k.sides[c]!.accepted >= MIN_ACCEPTED)) max = Math.max(max, p.total);
  }
  if (!max) for (const k of view.kinds) for (const c of CARRIERS) max = Math.max(max, perAccepted(k.sides[c])?.total ?? 0);
  const machine = view.busy + view.idle;
  const total = view.model + machine;
  const cols = compact ? 'grid-cols-[minmax(0,96px)_74px_minmax(0,1fr)_60px_12px]' : 'grid-cols-[minmax(0,150px)_86px_54px_minmax(0,1fr)_64px_12px]';
  const width = (x: number) => (grown && max ? `${Math.min(100, (x / max) * 100)}%` : '0%');
  const big = !v ? t('swarmOut.cost.cannot')
    : Math.abs(v.pct) < 1 ? t('swarmOut.cost.even')
      : t(layer === 'all' ? 'swarmOut.cost.saves' : 'swarmOut.cost.modelLower', {
        who: t(v.pct > 0 ? 'swarmOut.board.elastic' : 'swarmOut.board.ec2'), pct: Math.round(Math.abs(v.pct)) });
  const side = (row: KindRow, c: Carrier, first: boolean) => {
    const s = row.sides[c];
    if (!s) return null;
    const kind = row.kind;
    const p = perAccepted(s);
    const few = s.accepted < MIN_ACCEPTED;
    // the cheaper side of a kind both can be compared on, with machine
    const other = perAccepted(row.sides[c === 'ec2' ? 'agentcore' : 'ec2']);
    const low = layer === 'all' && comparable(row) && !!p && !!other && p.total < other.total;
    return (
      <div className={`grid ${cols} gap-x-3 items-center px-2 py-[7px] text-body`} data-testid="cost-side" data-carrier={c} data-kind={kind}>
        <span className="truncate font-semibold" title={kind}>{first ? kind : ''}</span>
        <span><Kind ac={c === 'agentcore'}>{t(c === 'agentcore' ? 'swarmOut.board.elastic' : 'swarmOut.board.ec2')}</Kind></span>
        {!compact && <span className={`text-right tabular-nums ${muted}`}>{s.accepted} / {s.n}</span>}
        <span className="flex h-2.5 rounded-sm overflow-hidden" style={{ opacity: few ? 0.3 : 1 }} aria-hidden>
          {p && <>
            <span className="so-width block h-full" style={{ width: width(p.model), background: SEG.model }} />
            {layer === 'all' && <span className="so-width block h-full" style={{ width: width(p.busy), background: SEG.busy }} />}
            {layer === 'all' && <span className="so-width block h-full" style={{ width: width(p.idle), background: SEG.idle }} />}
          </>}
        </span>
        <span className={`text-right tabular-nums ${!p || few ? `text-small font-normal ${dim}` : 'font-semibold'}`}
          style={low ? { color: 'var(--color-status-success)' } : undefined} data-testid="cost-per-accepted" data-low={low || undefined}>
          {!p ? t('swarmOut.cost.noneAccepted') : few ? t('swarmOut.cost.few') : money(p.total)}
        </span>
        <span className={`text-center ${dim} transition-transform`} aria-hidden style={{ transform: first && open.has(kind) ? 'rotate(90deg)' : undefined }}>{first ? '›' : ''}</span>
      </div>
    );
  };
  return (
    <section className={`${card} px-[18px] py-4 mt-8`} data-testid="history-cost" data-layer={layer}>
      <div className="flex items-center gap-2.5 mb-3">
        <span className="text-body font-semibold">{t('swarmOut.cost.title')}</span>
        <span className="ml-auto inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)]" role="group" aria-label={t('swarmOut.cost.title')} data-testid="cost-layer">
          {(['all', 'model'] as const).map((l) => (
            <button key={l} type="button" aria-pressed={layer === l} onClick={() => setLayer(l)}
              className={`px-3 py-[3px] rounded-md text-small transition-colors ${layer === l ? 'bg-[var(--color-card)] text-[var(--color-text)] font-semibold shadow-sm' : `${muted} hover:text-[var(--color-text)]`}`}>
              {t(`swarmOut.cost.layer.${l}`)}
            </button>
          ))}
        </span>
      </div>
      <div className="flex items-baseline flex-wrap gap-x-3 gap-y-1">
        <span className={`font-semibold tabular-nums ${v ? 'text-[28px] leading-[34px]' : 'text-[20px] leading-[28px] text-[var(--color-text-secondary)]'}`}
          style={v && layer === 'all' && Math.abs(v.pct) >= 1 ? { color: 'var(--color-status-success)' } : undefined} data-testid="cost-verdict" data-pct={v ? Math.round(v.pct) : undefined}>
          {big}
        </span>
        <span className="text-heading flex-1 min-w-[200px]">
          {v ? t(layer === 'all' ? 'swarmOut.cost.whatAll' : 'swarmOut.cost.whatModel') : t('swarmOut.cost.cannotWhy', { count: acRuns, min: MIN_ACCEPTED })}
        </span>
        {v && <span className="text-small px-2 py-px rounded-full bg-[var(--color-bg-chrome)] text-[var(--color-text-muted)] whitespace-nowrap">{t('swarmOut.cost.compared', { kinds: v.kinds, count: v.accepted })}</span>}
      </div>
      <div className={`mt-2.5 text-small ${muted}`} data-testid="cost-facts">
        {layer === 'all'
          ? t('swarmOut.cost.factsAll', { total: money(total), model: money(view.model), machine: money(machine), share: total ? Math.round((machine / total) * 100) : 0, idle: money(view.idle) })
          : t('swarmOut.cost.factsModel', { model: money(view.model), out: tok(view.tokens.output), hit: Math.round(hit(view.tokens) * 100) })}
      </div>
      {layer === 'all' && view.onHours > 0 && (
        <div className={`mt-0.5 text-small ${muted} flex items-center gap-1.5`} data-testid="cost-busy">
          {t('swarmOut.cost.busy')}
          <span className="relative inline-block w-16 h-1.5 rounded-sm overflow-hidden" style={{ background: SEG.idle }} aria-hidden>
            <span className="so-width absolute inset-y-0 left-0" style={{ width: grown ? `${(view.activeHours / view.onHours) * 100}%` : '0%', background: SEG.busy }} />
          </span>
          <b className="font-semibold text-[var(--color-text)] tabular-nums">{Math.round((view.activeHours / view.onHours) * 100)}%</b>
          <span>{t('swarmOut.cost.busyHours', { on: Math.round(view.onHours).toLocaleString('en-US'), active: view.activeHours.toFixed(1) })}</span>
        </div>
      )}
      <div className={`flex gap-3.5 mt-[18px] mb-1.5 text-meta ${muted}`}>
        <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2 rounded-sm" style={{ background: SEG.model }} />{t('swarmOut.cost.seg.model')}</span>
        {layer === 'all' && <>
          <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2 rounded-sm" style={{ background: SEG.busy }} />{t('swarmOut.cost.seg.busy')}</span>
          <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2 rounded-sm" style={{ background: SEG.idle }} />{t('swarmOut.cost.seg.idle')}</span>
        </>}
      </div>
      <div className={`grid ${cols} gap-x-3 px-2 pb-1 text-meta ${dim}`}>
        <span>{t('swarmOut.cost.col.kind')}</span><span>{t('swarmOut.cost.col.where')}</span>
        {!compact && <span className="text-right">{t('swarmOut.cost.col.accepted')}</span>}
        <span>{t('swarmOut.cost.col.bar')}</span><span className="text-right">{t('swarmOut.cost.col.per')}</span><span />
      </div>
      {view.kinds.length === 0 && <div className={`px-2 py-2 text-body ${muted}`}>{t('swarmOut.cost.empty')}</div>}
      {view.kinds.map((k, i) => {
        const has = CARRIERS.filter((c) => k.sides[c]);
        const isOpen = open.has(k.kind);
        return (
          <div key={k.kind} className={`-mx-2 ${i ? 'border-t border-[var(--color-border)]' : ''}`} data-testid="cost-kind" data-kind={k.kind}>
            <button type="button" aria-expanded={isOpen} className="block w-full text-left rounded-md hover:bg-[var(--color-hover)] transition-colors"
              onClick={() => setOpen((o) => { const n = new Set(o); if (n.has(k.kind)) n.delete(k.kind); else n.add(k.kind); return n; })}>
              {has.map((c, j) => <div key={c}>{side(k, c, j === 0)}</div>)}
            </button>
            {isOpen && (
              <div className={`so-view-in grid grid-cols-[74px_repeat(5,minmax(0,1fr))] gap-x-3 gap-y-1 pb-3 text-small tabular-nums text-[var(--color-text-secondary)] ${compact ? 'px-2' : 'pl-[170px] pr-2'}`} data-testid="cost-tokens">
                <span />
                {(['input', 'output', 'cache_read', 'cache_write'] as const).map((f) => <span key={f} className={`text-right text-meta ${dim}`}>{t(`swarmOut.cost.tok.${f}`)}</span>)}
                <span className={`text-right text-meta ${dim}`}>{t('swarmOut.cost.tok.hit')}</span>
                {has.map((c) => {
                  const s = k.sides[c]!;
                  const d = Math.max(1, s.accepted);
                  return [
                    <span key={`${c}-k`}><Kind ac={c === 'agentcore'}>{t(c === 'agentcore' ? 'swarmOut.board.elastic' : 'swarmOut.board.ec2')}</Kind></span>,
                    ...(['input', 'output', 'cache_read', 'cache_write'] as const).map((f) => <span key={`${c}-${f}`} className="text-right">{tok(s.tokens[f] / d)}</span>),
                    <span key={`${c}-h`} className="text-right">{Math.round(hit(s.tokens) * 100)}%</span>,
                  ];
                })}
                <span className={`col-span-6 text-meta ${dim} mt-0.5`}>{t('swarmOut.cost.tokNote')}</span>
              </div>
            )}
          </div>
        );
      })}
      <div className={`mt-2.5 text-meta ${dim}`} data-testid="cost-note">
        {layer === 'all'
          ? <>
            {!view.hasMachines ? t('swarmOut.cost.noRecord') : view.recordedSince ? t('swarmOut.cost.recordedSince', { at: stamp(view.recordedSince) }) : t('swarmOut.cost.noEc2Record')}
            {view.leftOut > 0 && <> {t('swarmOut.cost.leftOut', { count: view.leftOut })}</>}
            {' '}{t('swarmOut.cost.estimate')}
          </>
          : t('swarmOut.cost.modelNote')}
      </div>
    </section>
  );
}
