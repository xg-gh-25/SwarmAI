/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * shows the Hives and the runs sent to them, on real data only (design
 * docs/design/2026-10-28-swarm-out-live-dashboard.md). Two views:
 *  - 运行 (LiveDispatches): the questions waiting, the fleet and its free slots, the
 *    runs by state.
 *  - 历史: runs sent per day and the last week's runs by Hive, from the dispatch log.
 *    The cross-run audit (verdict, five dimensions, by type, occupancy) and grouping by
 *    brain or Project Agent have no data source yet: each says TBD and what is missing.
 *
 * The fleet (/api/swarm-out/fleet) is polled here while the panel is shown: it gives the
 * header's Hive count and decides whether the setup guide (HiveGuide) replaces the run
 * view (no Hive running). Responsive: the panel measures its own width (it shares the
 * row with the chat column and the Canvas); below COMPACT_BELOW px type steps down.
 */
import { useLayoutEffect, useRef, useState } from 'react';
import type { SwarmOutRun } from '../../services/swarmOut';
import { HiveGuide } from './HiveGuide';
import { LiveDispatches, Tbd } from './LiveDispatches';
import { dailySent, runsByHive, stateLabel, useFleet, useRuns } from './live';

/** Below this panel width (CSS px) the dashboard uses its compact layout. */
export const COMPACT_BELOW = 820;

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const muted = 'text-[var(--color-text-muted)]';

/** The panel's own content width, tracked with a ResizeObserver (0 before layout,
 *  and in jsdom, which has no ResizeObserver: then the wide layout is used). */
function usePanelWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function when(iso: string): string {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? '' : new Date(t).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function DailyBars({ runs, now }: { runs: SwarmOutRun[]; now: number }) {
  const days = dailySent(runs, now);
  const max = Math.max(1, ...days.map((d) => d.count));
  return (
    <section className={card} data-testid="swarm-out-daily">
      <div className="flex items-baseline gap-2 px-4 pt-3 pb-1">
        <h3 className="m-0 text-[13px] font-semibold">每天派出的 run</h3>
        <span className="text-[12px] text-[var(--color-text-dim)]">近 14 天 · 按本机日期</span>
      </div>
      <div className="px-4 pb-3">
        <div className="flex items-end gap-[6px] h-[90px] border-b border-[var(--color-border)]">
          {days.map((d) => (
            <div key={d.label} className="flex-1 flex flex-col justify-end h-full" title={`${d.label} · 派出 ${d.count} 个`} data-testid="swarm-out-day" data-count={d.count}>
              <div className="rounded-t bg-[var(--color-chart-1)]" style={{ height: `${(d.count / max) * 100}%` }} />
            </div>
          ))}
        </div>
        <div className={`flex justify-between text-[10.5px] mt-1 ${muted}`}>
          <span>{days[0].label}</span><span>{days[days.length - 1].label}</span>
        </div>
      </div>
    </section>
  );
}

function History({ hidden, now }: { hidden: boolean; now: number }) {
  const { data, failed } = useRuns(hidden);
  const runs = data?.runs ?? [];
  const groups = runsByHive(runs, now);
  return (
    <div data-testid="swarm-out-history">
      {failed && <div className={`text-[12px] mb-2 ${muted}`}>读不到派发记录，稍后自动重试</div>}
      {data ? <DailyBars runs={runs} now={now} /> : !failed && <div className={`text-[12px] ${muted}`}>正在读…</div>}
      <section className="mt-6" data-testid="run-history">
        <div className="flex items-baseline gap-2 px-0.5">
          <h3 className="m-0 text-[13px] font-semibold">Run 记录</h3>
          <span className="text-[12px] text-[var(--color-text-dim)]">近 7 天 · 按 Hive</span>
        </div>
        <div className="mt-1 text-[12px] text-[var(--color-text-dim)] px-0.5">按大脑 / Project Agent 分组 · TBD：派发记录里没有大脑和 Project Agent</div>
        {data && groups.length === 0 && <div className={`mt-2 text-[12.5px] ${muted}`}>近 7 天没有派出的 run</div>}
        {groups.map((g) => (
          <div key={g.hive} className="mt-4" data-testid="history-group" data-group={g.hive}>
            <div className="flex items-baseline gap-2 px-0.5 mb-1.5">
              <h4 className="m-0 text-[13px] font-semibold">{g.hive}</h4>
              <span className="text-[12px] text-[var(--color-text-dim)] tabular-nums" data-testid="history-group-count">{g.runs.length} 个 run</span>
            </div>
            <ul className={`${card} m-0 list-none px-1.5 py-0.5`}>
              {g.runs.map((r) => (
                <li key={r.dispatch_id} className="px-2 py-2 border-t border-[var(--color-border)] first:border-t-0 min-w-0" data-testid="history-run">
                  <div className="flex items-start gap-2 min-w-0">
                    <span className="min-w-0 flex-1 line-clamp-2 leading-[18px]" title={r.title}>{r.title}</span>
                    <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{when(r.sent_at)}</span>
                  </div>
                  <div className={`text-[11.5px] mt-0.5 ${muted}`}>{stateLabel(r)}</div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
      <Tbd title="一句话结论" missing="没有跨 run 的审计汇总" testId="swarm-out-tbd-verdict" />
      <Tbd title="五个维度" missing="Run Isolation · Quality · Autonomy · Speed · Token Cost 要从每个 run 的审计汇总，本机还没取回审计" testId="swarm-out-tbd-dimensions" />
      <Tbd title="工蜂占用" missing="没有按时间采样每台 Hive 的并发" testId="swarm-out-tbd-occupancy" />
      <Tbd title="按类型" missing="派发记录不带任务类型" testId="swarm-out-tbd-type" />
    </div>
  );
}

export function SwarmOutDashboard({ hidden = false, onOpenHive, now }: { hidden?: boolean; onOpenHive?: () => void; now?: number }) {
  const [view, setView] = useState<'run' | 'history'>('run');
  const rootRef = useRef<HTMLDivElement>(null);
  const width = usePanelWidth(rootRef);
  const compact = width > 0 && width < COMPACT_BELOW;
  const padX = compact ? 'px-4' : 'px-6';
  const { data: fleet, failed: fleetFailed } = useFleet(hidden);
  const at = now ?? Date.now();
  const seg = (v: 'run' | 'history', label: string) => (
    <button
      type="button"
      onClick={() => setView(v)}
      aria-pressed={view === v}
      className={`px-3 py-[3px] rounded-md text-[12.5px] ${view === v ? 'bg-[var(--color-card)] text-[var(--color-text)] font-medium shadow-sm' : muted}`}
    >{label}</button>
  );
  return (
    <div
      ref={rootRef}
      className={`min-w-0 flex flex-col min-h-0 bg-[var(--color-bg)] ${compact ? 'text-[12.5px]' : 'text-[13px]'}`}
      style={{ flex: '1 1 0', display: hidden ? 'none' : undefined }}
      data-testid="swarm-out-dashboard"
      data-compact={compact || undefined}
    >
      <div className={`flex items-center gap-3 ${padX} ${compact ? 'pt-3' : 'pt-4'} flex-shrink-0 min-w-0`}>
        <h2 className={`m-0 ${compact ? 'text-[15px]' : 'text-[17px]'} font-semibold whitespace-nowrap`}>Swarm Out</h2>
        <div className="inline-flex rounded-lg p-[2px] bg-[var(--color-bg-chrome)] flex-shrink-0">{seg('run', '运行')}{seg('history', '历史')}</div>
        {fleet !== null && (
          <span className={`ml-auto text-[12px] truncate min-w-0 ${muted}`} data-testid="swarm-out-hive-count">
            {fleet.running > 0
              ? <><span className="text-[var(--color-status-success)]">{fleet.running} 台 Hive 在运行</span>，合上 Mac 也会继续</>
              : fleet.hives.length > 0 ? `${fleet.hives.length} 台 Hive，都没在运行` : '还没有 Hive'}
          </span>
        )}
      </div>
      <div className={`flex-1 overflow-y-auto overflow-x-hidden ${padX} ${compact ? 'pt-3 pb-5' : 'pt-4 pb-6'}`}>
        {view === 'run'
          ? (fleet !== null && fleet.running === 0
            ? <HiveGuide hives={fleet.hives} onOpenHive={onOpenHive} />
            : <LiveDispatches hidden={hidden} fleet={fleet} fleetFailed={fleetFailed} onOpenHive={onOpenHive} now={at} />)
          : <History hidden={hidden} now={at} />}
      </div>
    </div>
  );
}
