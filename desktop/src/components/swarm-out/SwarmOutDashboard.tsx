/**
 * Swarm Out dashboard — the left side of the pinned Swarm Out tab (pages/chat/swarmOut.ts).
 *
 * The right side is the real chat column ChatPage renders for every tab; this panel
 * only shows the fleet and the runs. Two views:
 *  - 运行: what can be sent out now (idle workers), the fleet, then the runs by what
 *    they ask of the user — 等你 → 在跑 → 已安排 → 24 小时内结束.
 *  - 历史: one verdict sentence, the five audit dimensions side by side (no composite
 *    score), runs per day, worker occupancy, and a breakdown by run type.
 *
 * Responsive: the panel measures its own width (it shares the row with the chat
 * column, whose width the user drags, and the Canvas), not the window. Below
 * COMPACT_BELOW px it switches to a compact layout: the run tables become stacked rows,
 * the KPI tiles go 2×2, and type and spacing step down one size, so a narrow dashboard
 * stays readable instead of truncating every title to a few characters.
 *
 * Data is STATIC for now (./mockData.ts) — the conductor, record layer and cross-run
 * audit aggregate it will read do not exist yet. A banner says so, so a sample number
 * is never read as a live one.
 */
import { createContext, Fragment, useContext, useLayoutEffect, useRef, useState } from 'react';
import { JudgeTag } from './JudgeTag';
import { TraceTags } from './TraceTags';
import {
  BY_TYPE, DAILY_COMPLETED, DAILY_LABELS, ENDED, FLEET, HISTORY_DIMENSIONS, HISTORY_VERDICT,
  OCCUPANCY, OCCUPANCY_DAYS, RUNNING, SCHEDULED, WAITING, type RunRow, type RunTrace,
} from './mockData';

/** Below this panel width (CSS px) the dashboard uses its compact layout. */
export const COMPACT_BELOW = 820;
/** Below this the four KPI tiles go 2×2 instead of 3 + 1. */
const KPI_ROW_MIN = 560;

const Compact = createContext(false);

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

const card = 'rounded-xl border border-[var(--color-border)] bg-[var(--color-card)]';
const cardHead = 'flex items-baseline gap-2 px-4 pt-3 pb-1';
const muted = 'text-[var(--color-text-muted)]';

function Dot({ state }: { state: RunRow['state'] }) {
  const base = 'inline-block w-[7px] h-[7px] rounded-full flex-shrink-0';
  if (state === 'running') return <span className={`${base} bg-[var(--color-status-success)] animate-pulse`} />;
  if (state === 'waiting') return <span className={`${base} bg-[var(--color-status-warning)]`} />;
  if (state === 'done') return <span className={`${base} border-[1.5px] border-[var(--color-status-success)]`} />;
  if (state === 'failed') return <span className={`${base} border-[1.5px] border-[var(--color-text-muted)]`} />;
  return <span className={`${base} bg-[var(--color-text-dim)]`} />;
}

/** Start → end · duration · tokens. A run still going has no end yet: it shows the
 *  start, how long it has run and how long it is expected to take, never a "—"
 *  pretending to be a value. (The estimate lives here, with the other times, rather
 *  than in its own column: the row's first line has no room for it on a narrow
 *  dashboard.) */
function TraceMeta({ trace, eta }: { trace: RunTrace; eta?: string }) {
  const span = trace.startedAt ? (trace.endedAt ? `${trace.startedAt} → ${trace.endedAt}` : `${trace.startedAt} 开始`) : null;
  return (
    <span className={`text-[11.5px] whitespace-nowrap tabular-nums ${muted}`} data-testid="trace-meta">
      {[span, trace.duration, eta, `${trace.tokens} tokens`].filter(Boolean).join(' · ')}
    </span>
  );
}

/** Columns are decided once per table and every row renders every one of them, so
 *  cells line up; `table-fixed` + a title column that takes the rest makes the
 *  title and sub lines truncate instead of widening the table past its card.
 *  A run with a trace gets a second line spanning every column after the dot: the
 *  trace tags (what was prepared, what came back) on the left, time and tokens on
 *  the right. Scheduled runs have not been prepared yet, so they have no second line. */
function RunTable({ rows, empty }: { rows: RunRow[]; empty: string }) {
  const compact = useContext(Compact);
  if (rows.length === 0) return <div className={`px-3 py-3 text-[12.5px] ${muted}`}>{empty}</div>;
  if (compact) return <RunList rows={rows} />;
  const has = {
    stage: rows.some((r) => r.stage),
    flag: rows.some((r) => r.flag),
    action: rows.some((r) => r.action),
    judge: rows.some((r) => r.judge),
  };
  // the trace line spans every column after the dot
  const span = 2 + Number(has.stage) + Number(has.flag) + Number(has.action) + Number(has.judge);
  // a row followed by its trace line keeps its top padding and hands the bottom to the trace line
  const pad = (r: RunRow) => (r.trace ? 'pt-2.5 pb-1' : 'py-2.5');
  return (
    <table className="w-full border-collapse table-fixed">
      <colgroup>
        <col style={{ width: 22 }} />
        <col />
        {has.stage && <col style={{ width: 136 }} />}
        {has.flag && <col style={{ width: 84 }} />}
        {has.action && <col style={{ width: 52 }} />}
        {has.judge && <col style={{ width: 196 }} />}
        <col style={{ width: 96 }} />
      </colgroup>
      <tbody>
        {rows.map((r, i) => (
          <Fragment key={r.id}>
          <tr className={i > 0 ? 'border-t border-[var(--color-border)]' : ''} data-testid="run-row">
            <td className={`pl-2 pr-1 align-middle ${pad(r)}`}><Dot state={r.state} /></td>
            <td className={`px-2 overflow-hidden ${pad(r)}`}>
              <div className="font-medium truncate" title={r.title}>{r.title}</div>
              <div className={`text-[12px] ${muted} truncate`} title={r.sub}>{r.sub}</div>
            </td>
            {has.stage && (
              <td className={`px-2 text-[12px] whitespace-nowrap overflow-hidden text-[var(--color-text-secondary)] ${pad(r)}`}>
                {r.stage && <><b className="font-semibold">{r.stage.now}</b>{' '}<span className="text-[var(--color-text-dim)]">→ {r.stage.next}</span></>}
              </td>
            )}
            {has.flag && <td className={`px-2 text-[12px] font-semibold whitespace-nowrap text-[var(--color-status-warning)] ${pad(r)}`}>{r.flag}</td>}
            {/* Not wired yet: shown as plain text, not a link that does nothing. */}
            {has.action && <td className={`px-2 text-[12.5px] whitespace-nowrap ${muted} ${pad(r)}`}>{r.action}</td>}
            {has.judge && <td className={`px-2 whitespace-nowrap ${pad(r)}`}>{r.judge && <JudgeTag judge={r.judge} title={r.title} />}</td>}
            <td className={`pl-2 pr-3 text-[12px] whitespace-nowrap text-right tabular-nums ${muted} ${pad(r)}`}>{r.worker ?? r.when}</td>
          </tr>
          {r.trace && (
            <tr data-testid="run-trace-row">
              <td />
              <td colSpan={span} className="pl-1 pr-3 pb-2.5 pt-0.5">
                {/* narrow dashboard: time and tokens wrap under the tags, still right-aligned */}
                <div className="flex items-center flex-wrap gap-x-3 gap-y-0.5 min-w-0">
                  <TraceTags trace={r.trace} />
                  <span className="ml-auto"><TraceMeta trace={r.trace} eta={r.eta?.text} /></span>
                </div>
              </td>
            </tr>
          )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

/** Compact layout of a run table: one stacked block per run. The title gets the
 *  whole first line (worker / time on its right); the flag, stage and Autonomy Judge
 *  move to a line of their own, then the trace line, so nothing squeezes the title. */
function RunList({ rows }: { rows: RunRow[] }) {
  return (
    <div>
      {rows.map((r, i) => (
        <div key={r.id} className={`px-2 py-2 ${i > 0 ? 'border-t border-[var(--color-border)]' : ''}`} data-testid="run-row">
          <div className="flex items-center gap-2 min-w-0">
            <Dot state={r.state} />
            <span className="font-medium truncate min-w-0 flex-1" title={r.title}>{r.title}</span>
            <span className={`text-[11.5px] whitespace-nowrap tabular-nums flex-shrink-0 ${muted}`}>{r.worker ?? r.when}</span>
          </div>
          <div className={`text-[11.5px] ${muted} truncate pl-[15px]`} title={r.sub}>{r.sub}</div>
          {(r.flag || r.stage || r.action || r.judge) && (
            <div className="flex items-center flex-wrap gap-x-3 gap-y-1 pl-[15px] mt-1 text-[11.5px]">
              {r.flag && <span className="font-semibold whitespace-nowrap text-[var(--color-status-warning)]">{r.flag}</span>}
              {r.stage && (
                <span className="whitespace-nowrap text-[var(--color-text-secondary)]">
                  <b className="font-semibold">{r.stage.now}</b>{' '}<span className="text-[var(--color-text-dim)]">→ {r.stage.next}</span>
                </span>
              )}
              {r.action && <span className={`whitespace-nowrap ${muted}`}>{r.action}</span>}
              {r.judge && <JudgeTag judge={r.judge} title={r.title} />}
            </div>
          )}
          {r.trace && (
            <div className="flex items-center flex-wrap gap-x-3 gap-y-0.5 pl-[15px] mt-1 min-w-0" data-testid="run-trace-row">
              <TraceTags trace={r.trace} />
              <span className="ml-auto"><TraceMeta trace={r.trace} eta={r.eta?.text} /></span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Section({ title, count, aside, children }: { title: string; count?: number; aside?: string; children: React.ReactNode }) {
  const compact = useContext(Compact);
  return (
    <section className={`${card} ${compact ? 'mt-3' : 'mt-4'}`}>
      <div className={cardHead}>
        <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
        {count != null && <span className="text-[12px] text-[var(--color-text-dim)]">{count}</span>}
        {aside && <span className={`ml-auto text-[12px] ${muted}`}>{aside}</span>}
      </div>
      <div className="px-2 pb-2">{children}</div>
    </section>
  );
}

function Overview({ width }: { width: number }) {
  const compact = useContext(Compact);
  const kpiCols = width > 0 && width < KPI_ROW_MIN ? 2 : 4;
  const big = compact ? 'text-[24px]' : 'text-[30px]';
  const num = compact ? 'text-[21px]' : 'text-[26px]';
  const lab = compact ? 'text-[11.5px] mt-1' : 'text-[12.5px] mt-1.5';
  const tile = `${card} ${compact ? 'px-3 py-2.5' : 'px-4 py-3.5'}`;
  const total = FLEET.filter((h) => h.healthy).reduce((n, h) => n + h.slots, 0);
  const idle = FLEET.filter((h) => h.healthy).reduce((n, h) => n + h.slots - h.busy, 0);
  // e.g. "1 个要你回答 · 1 个落不落地", from the rows' own flags
  const flagCounts = new Map<string, number>();
  for (const r of WAITING) if (r.flag) flagCounts.set(r.flag, (flagCounts.get(r.flag) ?? 0) + 1);
  const waitingBreakdown = [...flagCounts].map(([flag, n]) => `${n} 个${flag}`).join(' · ');
  return (
    <div data-testid="swarm-out-overview">
      {/* Always one row of four, or 2×2 when narrow: auto-fit gave a lone 3 + 1. */}
      <div className={`grid ${compact ? 'gap-2' : 'gap-3'}`} style={{ gridTemplateColumns: `repeat(${kpiCols}, minmax(0, 1fr))` }} data-testid="kpi-grid">
        <div className={tile}>
          <div className={`${big} font-semibold leading-none tabular-nums`}>{idle}<span className={`${compact ? 'text-[12.5px]' : 'text-[14px]'} font-medium ml-1 ${muted}`}>/ {total} 可用</span></div>
          <div className={`${lab} ${muted}`}>工蜂空闲，可以马上派</div>
        </div>
        <div className={tile}><div className={`${num} font-semibold leading-none`}>{RUNNING.length}</div><div className={`${lab} ${muted}`}>在跑</div></div>
        <div className={tile}><div className={`${num} font-semibold leading-none ${WAITING.length ? 'text-[var(--color-status-warning)]' : ''}`}>{WAITING.length}</div><div className={`${lab} ${muted}`}>等你{waitingBreakdown && `（${waitingBreakdown}）`}</div></div>
        <div className={tile}><div className={`${num} font-semibold leading-none`}>{ENDED.length}</div><div className={`${lab} ${muted}`}>24 小时内结束</div></div>
      </div>

      <section className={`${card} ${compact ? 'mt-3' : 'mt-4'}`}>
        <div className={cardHead}><h3 className="m-0 text-[13px] font-semibold">机群</h3><span className={`ml-auto text-[12px] ${muted}`}>槽位按内存算</span></div>
        {/* Slots shrink with the panel; the status keeps one line and truncates (full text on hover). */}
        <div className={`grid grid-cols-[minmax(64px,110px)_minmax(0,1fr)_minmax(0,max-content)] items-center gap-x-3 gap-y-2 ${compact ? 'px-3' : 'px-4'} pt-1.5 pb-3`}>
          {FLEET.map((h) => (
            <div key={h.name} className="contents">
              <div className="font-medium min-w-0 truncate">{h.name}<div className={`text-[11.5px] font-normal truncate ${muted}`}>{h.instanceType}</div></div>
              <div className="flex gap-[4px] min-w-0" style={{ opacity: h.healthy ? 1 : 0.5 }}>
                {Array.from({ length: h.slots }, (_, i) => (
                  <span key={i} className={`flex-1 min-w-[8px] max-w-[26px] h-[12px] rounded-[3px] border ${i < h.busy ? 'bg-[var(--color-status-success)] border-[var(--color-status-success)]' : 'bg-[var(--color-hover)] border-[var(--color-border)]'}`} />
                ))}
              </div>
              <div className={`text-[12px] text-right truncate min-w-0 ${h.healthy ? muted : 'text-[var(--color-status-warning)]'}`} title={h.status}>{h.status}</div>
            </div>
          ))}
        </div>
      </section>

      {/* The counts of 等你 / 在跑 / 24 小时内结束 are the KPI tiles above; not repeated here. */}
      <Section title="等你"><RunTable rows={WAITING} empty="没有在等你的 run" /></Section>
      <Section title="在跑"><RunTable rows={RUNNING} empty="没有在跑的 run" /></Section>
      <Section title="已安排" count={SCHEDULED.length}><RunTable rows={SCHEDULED} empty="没有安排的 run" /></Section>
      <Section title="24 小时内结束" aside="更早的在「历史」"><RunTable rows={ENDED} empty="24 小时内没有结束的 run" /></Section>
    </div>
  );
}

function DailyBars() {
  const max = 6;
  return (
    <div className="px-4 pb-3">
      <div className={`flex gap-3.5 text-[12px] ${muted} mb-2`}>
        <span><i className="inline-block w-[10px] h-[10px] rounded-sm mr-1.5 align-[-1px] bg-[var(--color-chart-1-prev)]" />上周</span>
        <span><i className="inline-block w-[10px] h-[10px] rounded-sm mr-1.5 align-[-1px] bg-[var(--color-chart-1)]" />这周</span>
      </div>
      <div className="flex items-end gap-[6px] h-[130px] border-b border-[var(--color-border)]">
        {DAILY_COMPLETED.map((v, i) => (
          <div key={DAILY_LABELS[i]} className="flex-1 flex flex-col justify-end h-full" title={`${DAILY_LABELS[i]} · 完成 ${v} 个${i < 7 ? '（上周）' : ''}`}>
            <div className="rounded-t" style={{ height: `${(v / max) * 100}%`, background: i < 7 ? 'var(--color-chart-1-prev)' : 'var(--color-chart-1)' }} />
          </div>
        ))}
      </div>
      <div className={`flex justify-between text-[10.5px] mt-1 ${muted}`}>
        <span>{DAILY_LABELS[0]}</span><span>{DAILY_LABELS[6]}</span><span>{DAILY_LABELS[13]}</span>
      </div>
    </div>
  );
}

function Occupancy() {
  return (
    <div className="px-4 pb-3">
      <div className="grid gap-[2px] text-[11px]" style={{ gridTemplateColumns: '36px repeat(24, 1fr)' }}>
        {OCCUPANCY.map((row, di) => (
          <div key={OCCUPANCY_DAYS[di]} className="contents">
            <div className={`self-center ${muted}`}>{OCCUPANCY_DAYS[di]}</div>
            {row.map((v, hr) => (
              <div
                key={hr}
                className="h-[14px] rounded-[2px]"
                title={`${OCCUPANCY_DAYS[di]} ${String(hr).padStart(2, '0')}:00 · 同时在跑 ${v} / 8${v === 8 ? '（占满）' : ''}`}
                style={{ background: v === 0 ? 'var(--color-hover)' : `color-mix(in srgb, var(--color-chart-1) ${Math.round((v / 8) * 100)}%, var(--color-hover))` }}
              />
            ))}
          </div>
        ))}
      </div>
      <div className={`flex justify-between text-[11px] mt-1 pl-[38px] ${muted}`}><span>0 点</span><span>6</span><span>12</span><span>18</span><span>23</span></div>
      <div className={`text-[11.5px] mt-2 ${muted}`}>颜色越深同时在跑越多，最深 = 8 个槽占满</div>
    </div>
  );
}

function History() {
  const compact = useContext(Compact);
  return (
    <div data-testid="swarm-out-history">
      <p className={`m-0 ${compact ? 'text-[14px]' : 'text-[15px]'} leading-relaxed`}>{HISTORY_VERDICT}</p>
      <div className="grid gap-2.5 mt-3.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
        {HISTORY_DIMENSIONS.map((d) => {
          const total = d.pass + d.warn + d.block + (d.na ?? 0);
          return (
            <div key={d.name} className={`${card} px-3.5 py-3`}>
              <div className={`text-[11.5px] font-semibold tracking-wide ${muted}`}>
                {d.name} {d.role && <span className="font-normal text-[var(--color-text-dim)]">{d.role}</span>}
              </div>
              <div className="text-[24px] font-semibold leading-tight mt-1 tabular-nums">{d.value}<span className={`text-[13px] font-medium ml-0.5 ${muted}`}>{d.unit}</span></div>
              <div className={`text-[12px] whitespace-nowrap ${muted}`}>{d.label}</div>
              <div className="flex h-[6px] rounded overflow-hidden gap-[2px] mt-2.5">
                <i className="block bg-[var(--color-status-success)]" style={{ flex: d.pass }} />
                {d.warn > 0 && <i className="block bg-[var(--color-status-warning)]" style={{ flex: d.warn }} />}
                {d.block > 0 && <i className="block bg-[var(--color-status-error)]" style={{ flex: d.block }} />}
                {d.na ? <i className="block bg-[var(--color-border)]" style={{ flex: d.na }} /> : null}
              </div>
              <div className={`text-[11.5px] mt-1 whitespace-nowrap ${muted}`} title={`共 ${total} 个 run`}>
                <b className="text-[var(--color-status-success)]">✓{d.pass}</b>{' '}
                {d.warn > 0 && <b className="text-[var(--color-status-warning)]">!{d.warn} </b>}
                {d.block > 0 && <b className="text-[var(--color-status-error)]">✕{d.block} </b>}
                · {d.tail}
              </div>
            </div>
          );
        })}
      </div>
      <div className="grid gap-x-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        <section className={`${card} mt-4`}><div className={cardHead}><h3 className="m-0 text-[13px] font-semibold">每天完成的 run</h3><span className="text-[12px] text-[var(--color-text-dim)]">近 14 天</span></div><DailyBars /></section>
        <section className={`${card} mt-4`}><div className={cardHead}><h3 className="m-0 text-[13px] font-semibold">工蜂占用</h3><span className="text-[12px] text-[var(--color-text-dim)]">近 7 天 · 每小时同时在跑几个</span></div><Occupancy /></section>
      </div>
      <Section title="按类型" aside="近 7 天">
        <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px] min-w-[560px]">
          <thead>
            <tr className="text-[11.5px] text-[var(--color-text-dim)] text-left">
              <th className="font-medium px-2 py-1">类型</th><th className="font-medium px-2 py-1 text-right">次数</th>
              <th className="font-medium px-2 py-1 text-right">自主合格交付</th><th className="font-medium px-2 py-1 text-right">中位 active</th>
              <th className="font-medium px-2 py-1 text-right">中位 Token</th><th className="font-medium px-2 py-1">没达标的主要原因</th>
            </tr>
          </thead>
          <tbody>
            {BY_TYPE.map((r) => (
              <tr key={r.type} className="border-t border-[var(--color-border)]">
                <td className="px-2 py-2 font-medium">{r.type}</td><td className="px-2 py-2 text-right tabular-nums">{r.runs}</td>
                <td className={`px-2 py-2 text-right tabular-nums ${r.qualified === 'N/A' ? muted : ''}`}>{r.qualified}</td>
                <td className="px-2 py-2 text-right tabular-nums">{r.active}</td><td className="px-2 py-2 text-right tabular-nums">{r.tokens}</td>
                <td className={`px-2 py-2 ${muted}`}>{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </Section>
    </div>
  );
}

export function SwarmOutDashboard({ hidden = false }: { hidden?: boolean }) {
  const [view, setView] = useState<'run' | 'history'>('run');
  const rootRef = useRef<HTMLDivElement>(null);
  const width = usePanelWidth(rootRef);
  const compact = width > 0 && width < COMPACT_BELOW;
  const padX = compact ? 'px-4' : 'px-6';
  const seg = (v: 'run' | 'history', label: string) => (
    <button
      type="button"
      onClick={() => setView(v)}
      aria-pressed={view === v}
      className={`px-3 py-[3px] rounded-md text-[12.5px] ${view === v ? 'bg-[var(--color-card)] text-[var(--color-text)] font-medium shadow-sm' : muted}`}
    >{label}</button>
  );
  return (
    <Compact.Provider value={compact}>
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
        <span className={`ml-auto text-[12px] truncate min-w-0 ${muted}`} title="2 台 Hive · 全部在云端，可以合上 Mac">
          2 台 Hive · <span className="text-[var(--color-status-success)]">全部在云端，可以合上 Mac</span>
        </span>
      </div>
      <div className={`${compact ? 'mx-4 mt-2' : 'mx-6 mt-3'} rounded-lg px-3 py-1.5 text-[12px] bg-[var(--color-hover)] text-[var(--color-text-muted)] flex-shrink-0`}>
        示例数据：还没接 Hive。右边的对话是真的 chat session。
      </div>
      <div className={`flex-1 overflow-y-auto overflow-x-hidden ${padX} ${compact ? 'pt-3 pb-5' : 'pt-4 pb-6'}`}>
        {view === 'run' ? <Overview width={width} /> : <History />}
      </div>
    </div>
    </Compact.Provider>
  );
}
