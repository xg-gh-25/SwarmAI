/**
 * ToDoOverlay — the left-nav "ToDo" surface: a single sortable flat table.
 *
 * Redesigned run_7ccfe39f (was a 4-zone kanban + Flow/History toggle + 5 charts
 * + confirm/reject review closure — judged over-engineered). Now ONE table the
 * user reads at a glance:
 *
 *   ┌ FilterBar: Time range [7d|30d|90d|All] · Status chips ······· + New ToDo ┐
 *   ├ KPI row: Open · In Progress · Completed(range) · completion rate          ┤
 *   ├ Analytics strip (always visible): Weekly created-vs-completed bars +      ┤
 *   │                                   Source distribution horizontal bars     ┤
 *   ├ Table: Priority │ Title │ Source │ Status │ Created │ Updated │ Completed ┤
 *   │   click any header → toggle asc/desc; per-row Dispatch + Withdraw;        │
 *   │   click row → detail drawer                                               │
 *   └──────────────────────────────────────────────────────────────────────────┘
 *
 * DATA: fetch list(1000) + history(1000, windowDays) ONCE, merge+dedup by id
 * (prefer the newer updatedAt on collision), exclude status='deleted' entirely
 * (withdrawn = gone; the backend soft-delete does NOT filter deleted from list —
 * sqlite.py list_by_workspace — so we exclude client-side). One range-filtered
 * array feeds table + KPI + BOTH charts, so their口径 is always identical.
 *
 * ACTIONS: + New ToDo (inline form → create) · Dispatch (onDispatch + work-packet
 * inject, ChatPage-owned) · Withdraw (todosService.delete + optimistic removal).
 * Row click → detail drawer (work packet + 4 timestamps).
 *
 * PRESERVED CONTRACT (do not change — external consumers depend):
 *   • ToDoContent{onDispatch, close}  — overlaySurfaces.tsx
 *   • parseWorkPacket / WorkPacket    — ChatPage.tsx buildDispatchPrompt
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety).
 *
 * @exports ToDoContent, parseWorkPacket, WorkPacket
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { todosService } from '../../services/todos';
import { classifyLoadError } from '../../services/api';
import type { ToDo, Priority } from '../../types/todo';
import {
  deriveStatus, sortTodos, filterByRange, computeKpis, weeklyBuckets, sourceDist,
  type SortKey, type SortDir, type TodoStatusLabel,
} from './todoTable';
import { fmtTs, WorkbenchToolbar, OverlayDrawer } from './overlayShell';

export interface ToDoContentProps {
  /** Land a todo into a chat tab (inject + snapshot). Returns true if it landed
   *  (→ host closes the overlay) or false on needs-close (→ overlay stays open). */
  onDispatch: (todo: ToDo) => boolean;
  /** Host-owned close (called after a successful dispatch). */
  close: () => void;
}

/** Parse a todo's linked_context JSON string into a work-packet object.
 *  Guarded: null / empty / malformed / non-object → null. */
export interface WorkPacket {
  next_step?: string;
  acceptance?: string;
  files?: string[];
  design_docs?: string[];
  commits?: string[];
  sessions?: string[];
  memory_refs?: string[];
  blockers?: string[];
  notes?: string;
  [k: string]: unknown;
}
export function parseWorkPacket(linkedContext: string | null): WorkPacket | null {
  if (!linkedContext) return null;
  try {
    const p = JSON.parse(linkedContext);
    if (typeof p === 'object' && p !== null && !Array.isArray(p)) return p as WorkPacket;
    return null;
  } catch {
    return null;
  }
}

// ── Range + status filter options ───────────────────────────────────
type RangeOpt = { label: string; days: number | null };
const RANGES: RangeOpt[] = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: 'All', days: null },
];
const DEFAULT_RANGE_IDX = 1; // 30d

// Status chips filter the TABLE only (not the charts/KPI, which follow the range).
type StatusFilter = 'All' | 'Open' | TodoStatusLabel;
const STATUS_FILTERS: StatusFilter[] = ['All', 'Open', 'In Progress', 'Completed', 'Cancelled'];
// 'Open' = the actionable set (Pending). 'All' = everything.
function matchesStatusFilter(t: ToDo, f: StatusFilter): boolean {
  if (f === 'All') return true;
  const s = deriveStatus(t);
  if (f === 'Open') return s === 'Pending';
  return s === f;
}

/** Merge active-list + history rows: dedup by id, prefer the newer updatedAt on
 *  collision, drop soft-deleted rows (withdrawn = gone). */
function mergeRows(list: ToDo[], history: ToDo[]): ToDo[] {
  const byId = new Map<string, ToDo>();
  for (const t of [...list, ...history]) {
    if (t.status === 'deleted') continue;
    const prev = byId.get(t.id);
    if (!prev || (t.updatedAt ?? '') > (prev.updatedAt ?? '')) byId.set(t.id, t);
  }
  return [...byId.values()];
}

export function ToDoContent({ onDispatch, close }: ToDoContentProps) {
  const [rows, setRows] = useState<ToDo[]>([]);
  const [rangeIdx, setRangeIdx] = useState(DEFAULT_RANGE_IDX);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('Open');
  const [sortKey, setSortKey] = useState<SortKey>('created');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<ToDo | null>(null);
  const [creating, setCreating] = useState(false);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const windowDays = RANGES[rangeIdx].days;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // Fetch active + windowed terminal, once. history windowDays is a fetch-breadth
      // hint; the authoritative range filter is client-side on createdAt (below), so
      // table/KPI/charts share one口径. 'All' → no window (broad fetch).
      const [list, hist] = await Promise.all([
        todosService.list('swarmws', undefined, 1000),
        todosService.history(1000, windowDays ?? undefined),
      ]);
      setRows(mergeRows(list, hist.todos));
      setLoadErr(null);
    } catch (e) {
      setRows([]);
      setLoadErr(classifyLoadError(e, 'ToDos'));
    } finally {
      setLoading(false);
    }
  }, [windowDays]);

  useEffect(() => { void refresh(); }, [refresh]);

  // One range-filtered set feeds charts + KPI (口径统一). The table additionally
  // applies the status chip + sort.
  const rangeRows = useMemo(() => filterByRange(rows, windowDays), [rows, windowDays]);
  const kpis = useMemo(() => computeKpis(rangeRows), [rangeRows]);
  const weekly = useMemo(() => weeklyBuckets(rangeRows), [rangeRows]);
  const sources = useMemo(() => sourceDist(rangeRows), [rangeRows]);
  const tableRows = useMemo(
    () => sortTodos(rangeRows.filter((t) => matchesStatusFilter(t, statusFilter)), sortKey, sortDir),
    [rangeRows, statusFilter, sortKey, sortDir],
  );

  const onSort = useCallback((key: SortKey) => {
    // Compute the next direction from current state, then set BOTH at top level.
    // (A nested setSortDir inside the setSortKey updater is an impure updater —
    // StrictMode double-invokes it → the toggle fires twice → net no-op. Gate-2 HIGH.)
    const nextDir: SortDir = sortKey === key ? (sortDir === 'asc' ? 'desc' : 'asc') : (key === 'created' ? 'desc' : 'asc');
    setSortKey(key);
    setSortDir(nextDir);
  }, [sortKey, sortDir]);

  const handleDispatch = useCallback((todo: ToDo) => {
    const landed = onDispatch(todo);
    if (landed) {
      requestAnimationFrame(() => requestAnimationFrame(() => close()));
    }
  }, [onDispatch, close]);

  const handleWithdraw = useCallback(async (todo: ToDo) => {
    setActionErr(null);
    // optimistic removal
    setRows((prev) => prev.filter((t) => t.id !== todo.id));
    if (selected?.id === todo.id) setSelected(null);
    try {
      await todosService.delete(todo.id);
    } catch {
      setActionErr('Could not withdraw that ToDo — please try again.');
      void refresh(); // restore truth on failure
    }
  }, [selected, refresh]);

  const handleCreated = useCallback(() => {
    setCreating(false);
    void refresh();
  }, [refresh]);

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="todo-overlay">
      {/* FilterBar: range + status chips (left) · New ToDo (right) */}
      <WorkbenchToolbar
        gap={2}
        loading={loading}
        left={(
          <div className="flex items-center gap-3 flex-wrap">
            {/* Time range — segmented; filters table + charts */}
            <div className="flex items-center gap-0.5 rounded-md border border-[var(--color-border)] p-0.5" data-testid="todo-range">
              {RANGES.map((r, i) => (
                <button
                  key={r.label}
                  onClick={() => setRangeIdx(i)}
                  data-testid={`todo-range-${r.label}`}
                  className={`px-2 py-0.5 text-[11px] font-medium rounded transition-colors ${
                    rangeIdx === i ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                  }`}
                >{r.label}</button>
              ))}
            </div>
            {/* Status chips — filters table only */}
            <div className="flex items-center gap-1 flex-wrap" data-testid="todo-status-chips">
              {STATUS_FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setStatusFilter(f)}
                  data-testid={`todo-chip-${f.replace(/\s+/g, '-')}`}
                  className={`px-2 py-0.5 text-[11px] font-medium rounded-full border transition-colors ${
                    statusFilter === f
                      ? 'border-primary/50 bg-primary/10 text-primary'
                      : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                  }`}
                >{f}</button>
              ))}
            </div>
          </div>
        )}
        right={(
          <button
            onClick={() => { setSelected(null); setCreating(true); }}
            data-testid="todo-new-btn"
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors shrink-0"
          >
            <span className="material-symbols-outlined text-[15px]">add</span>New ToDo
          </button>
        )}
      />

      <GuideBanner />

      {/* KPI row */}
      <KpiRow kpis={kpis} rangeLabel={RANGES[rangeIdx].label} />

      {/* Analytics strip — always visible */}
      <AnalyticsStrip weekly={weekly} sources={sources} />

      {/* Fetch failure (distinct from empty) */}
      {loadErr && (
        <div
          data-testid="todo-load-error"
          className="mx-4 mt-2 flex items-center gap-2 rounded-md border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-3 py-2 text-[12px] text-[var(--color-text)]"
        >
          <span className="material-symbols-outlined text-[15px] text-[#d0524a]">error</span>
          <span className="flex-1">{loadErr}</span>
          <button
            data-testid="todo-load-retry"
            onClick={() => void refresh()}
            className="rounded px-2 py-0.5 text-[11px] font-medium text-white"
            style={{ background: '#d0524a' }}
          >Retry</button>
        </div>
      )}
      {actionErr && (
        <div
          data-testid="todo-action-error"
          className="mx-4 mt-2 flex items-center gap-2 rounded-md bg-[#d0524a]/10 px-3 py-2 text-[12px] text-[#d0524a]"
        >
          <span className="material-symbols-outlined text-[15px]">warning</span>
          <span className="flex-1">{actionErr}</span>
          <button onClick={() => setActionErr(null)} className="text-[11px] underline opacity-80 hover:opacity-100">dismiss</button>
        </div>
      )}

      {/* The table */}
      <TodoTable
        rows={tableRows}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={onSort}
        onDispatch={handleDispatch}
        onWithdraw={handleWithdraw}
        onSelect={setSelected}
      />

      {selected && <DetailDrawer todo={selected} onClose={() => setSelected(null)} />}
      {creating && <NewTodoForm onCreated={handleCreated} onCancel={() => setCreating(false)} />}
    </div>
  );
}

// ── KPI row ──────────────────────────────────────────────────────────

function KpiRow({ kpis, rangeLabel }: { kpis: ReturnType<typeof computeKpis>; rangeLabel: string }) {
  const pct = Math.round(kpis.completionRate * 100);
  return (
    <div className="shrink-0 flex items-center gap-6 px-4 py-2.5 border-b border-[var(--color-border)]" data-testid="todo-kpis">
      <Kpi label="Open" value={kpis.open} testid="kpi-open" />
      <Kpi label="In Progress" value={kpis.inProgress} testid="kpi-inprogress" />
      <Kpi label={`Completed · ${rangeLabel}`} value={kpis.completed} testid="kpi-completed" />
      <Kpi label="Completion" value={`${pct}%`} testid="kpi-rate" />
    </div>
  );
}

function Kpi({ label, value, testid }: { label: string; value: number | string; testid: string }) {
  return (
    <div className="flex flex-col" data-testid={testid}>
      <span className="text-[18px] font-semibold text-[var(--color-text)] leading-tight tabular-nums">{value}</span>
      <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{label}</span>
    </div>
  );
}

// ── Analytics strip (always visible) ─────────────────────────────────

function AnalyticsStrip({ weekly, sources }: {
  weekly: ReturnType<typeof weeklyBuckets>;
  sources: ReturnType<typeof sourceDist>;
}) {
  return (
    <div className="shrink-0 grid grid-cols-2 gap-4 px-4 py-3 border-b border-[var(--color-border)]" data-testid="todo-analytics">
      <div className="flex flex-col gap-1.5" data-testid="todo-weekly">
        <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Weekly · created vs completed</div>
        <WeeklyBars data={weekly} />
      </div>
      <div className="flex flex-col gap-1.5" data-testid="todo-sources">
        <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Source distribution</div>
        <SourceBars data={sources} />
      </div>
    </div>
  );
}

function WeeklyBars({ data }: { data: ReturnType<typeof weeklyBuckets> }) {
  if (data.length === 0) return <div className="text-[11px] text-[var(--color-text-faint)] py-2">No data in range</div>;
  const max = Math.max(1, ...data.map((d) => Math.max(d.created, d.completed)));
  return (
    <div className="flex items-end gap-1.5 h-16">
      {data.slice(-10).map((d) => (
        <div key={d.week} className="flex-1 flex flex-col justify-end gap-0.5" title={`${d.week}: ${d.created} created / ${d.completed} completed`}>
          <div className="flex items-end gap-0.5 h-full">
            <div className="flex-1 bg-[var(--color-text-faint)]/40 rounded-sm self-end" style={{ height: `${(d.created / max) * 100}%` }} />
            <div className="flex-1 bg-primary rounded-sm self-end" style={{ height: `${(d.completed / max) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SourceBars({ data }: { data: ReturnType<typeof sourceDist> }) {
  if (data.length === 0) return <div className="text-[11px] text-[var(--color-text-faint)] py-2">No data in range</div>;
  const total = Math.max(1, data.reduce((s, d) => s + d.count, 0));
  return (
    <div className="flex flex-col gap-1">
      {data.map((d) => (
        <div key={d.source} className="flex items-center gap-2 text-[10px]" title={`${d.source}: ${d.count}`}>
          <span className="w-16 shrink-0 text-[var(--color-text-muted)] truncate">{d.source}</span>
          <div className="flex-1 h-2.5 rounded-sm bg-[var(--color-bg)] overflow-hidden">
            <div className="h-full bg-primary/70 rounded-sm" style={{ width: `${(d.count / total) * 100}%` }} />
          </div>
          <span className="w-6 shrink-0 text-right font-mono text-[var(--color-text-faint)] tabular-nums">{d.count}</span>
        </div>
      ))}
    </div>
  );
}

// ── The sortable table ───────────────────────────────────────────────

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'priority', label: 'Priority' },
  { key: 'title', label: 'Title' },
  { key: 'source', label: 'Source' },
  { key: 'status', label: 'Status' },
  { key: 'created', label: 'Created' },
  { key: 'updated', label: 'Updated' },
  { key: 'completed', label: 'Completed' },
];

const PRIORITY_COLOR: Record<Priority, string> = {
  high: '#ef4444', medium: '#f59e0b', low: '#3b82f6', none: 'transparent',
};

function TodoTable({ rows, sortKey, sortDir, onSort, onDispatch, onWithdraw, onSelect }: {
  rows: ToDo[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (k: SortKey) => void;
  onDispatch: (t: ToDo) => void;
  onWithdraw: (t: ToDo) => void;
  onSelect: (t: ToDo) => void;
}) {
  return (
    <div className="flex-1 overflow-auto min-h-0" data-testid="todo-table">
      {rows.length === 0 ? (
        <EmptyTodoHint />
      ) : (
        <table className="w-full text-[12px]">
          <thead className="sticky top-0 bg-[var(--color-card)] text-[var(--color-text-muted)] z-[1]">
            <tr>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => onSort(c.key)}
                  data-testid={`todo-th-${c.key}`}
                  className="text-left px-3 py-2.5 font-medium cursor-pointer select-none hover:text-[var(--color-text)] whitespace-nowrap"
                >
                  {c.label}
                  <span className="ml-1 text-[10px] text-primary">{sortKey === c.key ? (sortDir === 'asc' ? '▲' : '▼') : ''}</span>
                </th>
              ))}
              <th className="text-right px-3 py-2.5 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <TodoRow key={t.id} todo={t} onDispatch={onDispatch} onWithdraw={onWithdraw} onSelect={onSelect} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TodoRow({ todo, onDispatch, onWithdraw, onSelect }: {
  todo: ToDo; onDispatch: (t: ToDo) => void; onWithdraw: (t: ToDo) => void; onSelect: (t: ToDo) => void;
}) {
  const status = deriveStatus(todo);
  return (
    <tr
      className="border-t border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] cursor-pointer"
      data-testid="todo-row"
      onClick={() => onSelect(todo)}
    >
      <td className="px-3 py-2.5">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: PRIORITY_COLOR[todo.priority] }} />
          <span className="text-[11px] text-[var(--color-text-muted)] capitalize">{todo.priority === 'none' ? '—' : todo.priority}</span>
        </span>
      </td>
      <td className="px-3 py-2.5 max-w-[300px] truncate">{todo.title}</td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-muted)]">{todo.sourceType}</td>
      <td className="px-3 py-2.5"><StatusBadge status={status} /></td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.createdAt)}</td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.updatedAt)}</td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.completedAt)}</td>
      {/* Actions must not open the drawer */}
      <td className="px-3 py-2.5 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
        <div className="inline-flex items-center gap-1">
          {status !== 'Completed' && status !== 'Cancelled' && (
            <RowBtn onClick={() => onDispatch(todo)} icon="play_arrow" label="Dispatch" primary testid="todo-action-dispatch" />
          )}
          <RowBtn onClick={() => onWithdraw(todo)} icon="delete" label="Withdraw" testid="todo-action-withdraw" />
        </div>
      </td>
    </tr>
  );
}

function StatusBadge({ status }: { status: TodoStatusLabel }) {
  const cls: Record<TodoStatusLabel, string> = {
    Pending: 'text-[var(--color-text-muted)] bg-[var(--color-hover)]',
    'In Progress': 'text-amber-400 bg-amber-500/10',
    Completed: 'text-emerald-400 bg-emerald-500/10',
    Cancelled: 'text-red-400 bg-red-500/10',
  };
  return <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${cls[status]}`}>{status}</span>;
}

function RowBtn({ onClick, icon, label, primary, testid }: { onClick: () => void; icon: string; label: string; primary?: boolean; testid: string }) {
  return (
    <button
      onClick={onClick}
      title={label}
      data-testid={testid}
      className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[11px] font-medium rounded transition-colors ${
        primary ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
      }`}
    >
      <span className="material-symbols-outlined text-[13px]">{icon}</span>{label}
    </button>
  );
}

/** Empty-table hint — the full how-to lives in the persistent GuideBanner. */
function EmptyTodoHint() {
  return (
    <div className="text-[12px] text-[var(--color-text-faint)] leading-relaxed px-4 py-10 text-center" data-testid="todo-empty-hint">
      No ToDos match this filter — ask Swarm in chat, or <span className="text-primary font-medium">+ New ToDo</span>.
    </div>
  );
}

/**
 * Persistent user-guide banner — leads with the AI-native path: chat handles
 * add / bulk-import / update / report; manual "+ New ToDo" is the fallback.
 */
function GuideBanner() {
  return (
    <div
      className="shrink-0 mx-4 mt-3 rounded-lg border border-primary/25 bg-primary/[0.06] px-3.5 py-2.5 flex items-start gap-2.5"
      data-testid="todo-guide-banner"
    >
      <span className="material-symbols-outlined text-[16px] text-primary mt-0.5 shrink-0">auto_awesome</span>
      <div className="flex-1 min-w-0 text-[11px] leading-relaxed text-[var(--color-text-muted)]">
        <span className="text-[var(--color-text)] font-medium">Just ask Swarm in chat</span> — the AI-native way.
        Say <span className="font-mono text-[var(--color-text)]">“add a todo to …”</span> and it captures full context
        (files, design docs, next step) automatically. Chat also handles{' '}
        <span className="text-[var(--color-text)]">bulk import</span> (e.g. “import my open tasks from another tool”),{' '}
        <span className="text-[var(--color-text)]">updates</span> (“mark the auth todo done”), and{' '}
        <span className="text-[var(--color-text)]">reports</span> (“what’s my throughput this week?”).
        <span className="text-[var(--color-text-faint)]"> Or add one manually with <span className="text-primary font-medium">+ New ToDo</span>.</span>
      </div>
    </div>
  );
}

// ── Detail drawer ───────────────────────────────────────────────────

function DetailDrawer({ todo, onClose }: { todo: ToDo; onClose: () => void }) {
  const wp = parseWorkPacket(todo.linkedContext);
  const priColor = PRIORITY_COLOR[todo.priority] === 'transparent' ? 'var(--color-text-faint)' : PRIORITY_COLOR[todo.priority];
  const status = deriveStatus(todo);
  return (
    <OverlayDrawer widthPx={360} maxWidthPct={70} z={10} testid="todo-detail-drawer">
      <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="mt-1 w-1.5 h-4 rounded-full shrink-0" style={{ background: priColor }} />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-[var(--color-text)] leading-snug break-words">{todo.title}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] font-mono text-[var(--color-text-faint)]">
            <span>{todo.sourceType}</span>
            <span>·</span>
            <span>{todo.priority}</span>
            <span>·</span>
            <span>{status}</span>
          </div>
        </div>
        <button onClick={onClose} data-testid="todo-drawer-close" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-4 text-[12px]">
        {todo.description && (
          <Section label="Description">
            <p className="text-[var(--color-text)] leading-relaxed whitespace-pre-wrap break-words">{todo.description}</p>
          </Section>
        )}

        <Section label="Timeline">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
            <TsRow label="Created" iso={todo.createdAt} />
            <TsRow label="Dispatched" iso={todo.dispatchedAt} />
            <TsRow label="Completed" iso={todo.completedAt} />
            <TsRow label="Reviewed" iso={todo.reviewedAt} />
          </dl>
          {todo.dispatchedTabLabel && (
            <div className="mt-1 text-[11px] font-mono text-[var(--color-text-muted)]">→ {todo.dispatchedTabLabel}</div>
          )}
        </Section>

        {wp ? (
          <>
            {wp.next_step && <Section label="Next step"><p className="text-[var(--color-text)] break-words">{wp.next_step}</p></Section>}
            {wp.acceptance && <Section label="Done when"><p className="text-[var(--color-text)] break-words">{wp.acceptance}</p></Section>}
            <MaterialList label="Files" items={wp.files} mono />
            <MaterialList label="Design docs" items={wp.design_docs} mono />
            <MaterialList label="Commits" items={wp.commits} mono />
            <MaterialList label="Memory refs" items={wp.memory_refs} mono />
            <MaterialList label="Blockers" items={wp.blockers} />
            {wp.notes && <Section label="Notes"><p className="text-[var(--color-text-muted)] break-words whitespace-pre-wrap">{wp.notes}</p></Section>}
          </>
        ) : (
          <div className="text-[11px] text-[var(--color-text-faint)] italic">No work-packet context attached.</div>
        )}
      </div>
    </OverlayDrawer>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{label}</div>
      {children}
    </div>
  );
}

function TsRow({ label, iso }: { label: string; iso: string | null }) {
  return (
    <>
      <dt className="text-[var(--color-text-faint)]">{label}</dt>
      <dd className={iso ? 'text-[var(--color-text)]' : 'text-[var(--color-text-faint)]'}>{fmtTs(iso)}</dd>
    </>
  );
}

function MaterialList({ label, items, mono }: { label: string; items?: string[]; mono?: boolean }) {
  if (!items || items.length === 0) return null;
  return (
    <Section label={label}>
      <ul className="flex flex-col gap-0.5">
        {items.map((it, i) => (
          <li key={i} className={`text-[var(--color-text)] break-all ${mono ? 'font-mono text-[11px]' : ''}`}>{it}</li>
        ))}
      </ul>
    </Section>
  );
}

// ── New ToDo inline form ────────────────────────────────────────────

function NewTodoForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<Priority>('none');
  const [description, setDescription] = useState('');
  const [nextStep, setNextStep] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const t = title.trim();
    if (!t) { setErr('Title is required.'); return; }
    setBusy(true); setErr(null);
    try {
      const ns = nextStep.trim();
      await todosService.create({
        // workspace_id is REQUIRED at the Pydantic schema level (ToDoCreate,
        // Field(...)) — FastAPI 422s before the manager's default can fire.
        workspaceId: 'swarmws',
        title: t,
        priority,
        sourceType: 'manual',
        description: description.trim() || undefined,
        linkedContext: ns ? JSON.stringify({ next_step: ns }) : undefined,
      });
      onCreated();
    } catch {
      setErr('Failed to create ToDo.');
      setBusy(false);
    }
  }, [title, priority, description, nextStep, onCreated]);

  return (
    <OverlayDrawer widthPx={360} maxWidthPct={70} z={20} testid="todo-new-form">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="flex-1 text-[13px] font-semibold text-[var(--color-text)]">New ToDo</span>
        <button onClick={onCancel} data-testid="todo-new-cancel" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 text-[12px]">
        <Field label="Title *">
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            data-testid="todo-new-title"
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none"
            placeholder="What needs doing?"
          />
        </Field>
        <Field label="Priority">
          <div className="flex gap-1">
            {(['high', 'medium', 'low', 'none'] as Priority[]).map((p) => (
              <button
                key={p}
                onClick={() => setPriority(p)}
                data-testid={`todo-new-pri-${p}`}
                className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${
                  priority === p ? 'border-primary/50 bg-primary/10 text-primary' : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                }`}
              >{p}</button>
            ))}
          </div>
        </Field>
        <Field label="Description">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            data-testid="todo-new-desc"
            rows={3}
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none resize-none"
            placeholder="Why this exists / background"
          />
        </Field>
        <Field label="Next step">
          <input
            value={nextStep}
            onChange={(e) => setNextStep(e.target.value)}
            data-testid="todo-new-nextstep"
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none"
            placeholder="Concrete first action"
          />
        </Field>
        {err && <div className="text-[11px] text-red-400" data-testid="todo-new-err">{err}</div>}
      </div>
      <div className="flex items-center gap-2 px-4 py-3 border-t border-[var(--color-border)] shrink-0">
        <button
          onClick={submit}
          disabled={busy}
          data-testid="todo-new-submit"
          className="flex-1 px-3 py-1.5 text-[12px] font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 disabled:opacity-50 transition-colors"
        >{busy ? 'Creating…' : 'Create ToDo'}</button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-[12px] font-medium rounded-md text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors"
        >Cancel</button>
      </div>
    </OverlayDrawer>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{label}</label>
      {children}
    </div>
  );
}
