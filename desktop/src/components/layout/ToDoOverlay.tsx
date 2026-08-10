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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { todosService } from '../../services/todos';
import { classifyLoadError } from '../../services/api';
import type { ToDo, Priority, ToDoAttachment } from '../../types/todo';
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

/** Merge a user's form edits INTO the existing work-packet JSON without dropping
 *  any key the form doesn't expose. This is the Gate-1 F6 safety property: the
 *  edit form exposes ONLY next_step, but buildDispatchPrompt (ChatPage) consumes
 *  9 keys (files/design_docs/commits/sessions/memory_refs/blockers/next_step/
 *  acceptance/notes) + possibly _missing_fields — a spread of the parsed existing
 *  packet preserves ALL of them (stronger than an explicit allow-list), so a
 *  system/dispatched todo never loses context on edit. `edits` overwrites only
 *  any key the form doesn't expose. Returns a JSON string for linkedContext, or
 *  undefined if the result is empty (no packet at all). `edits` overwrites only
 *  the named keys; every pre-existing key (incl. unknowns / _missing_fields) is
 *  carried through verbatim. */
function mergeWorkPacket(
  existing: string | null,
  edits: Partial<WorkPacket>,
): string | undefined {
  const base = parseWorkPacket(existing) ?? {};
  const merged: WorkPacket = { ...base };
  for (const [k, v] of Object.entries(edits)) {
    // An explicit '' / undefined clears that field; anything else sets it.
    if (v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) {
      delete merged[k];
    } else {
      merged[k] = v;
    }
  }
  const keys = Object.keys(merged);
  // Empty packet → return "" (NOT undefined). In edit mode this is an intentional
  // clear (user emptied the sole field); toSnakeCase skips `undefined`, so an
  // undefined here would silently drop the write and the old value would stick
  // (Gate-2 MED). An empty string IS sent, and the backend's `is not None` guard
  // persists it; parseWorkPacket("") → null on next read (a clean empty packet).
  if (keys.length === 0) return '';
  return JSON.stringify(merged);
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

/**
 * Segmented control — the ONE filter primitive shared by BOTH the Range and Status
 * groups (Method B). A single filled slider is the selected state (bg-primary +
 * white); everything else is a transparent tab. Because both groups render through
 * this one component they can never drift into inconsistent selected styles. The
 * bordered container is the visible group boundary (Gestalt common-region), so the
 * label no longer has to carry the grouping.
 */
function Segmented<T extends string>({
  label, options, value, onChange, testid, optionTestid,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  testid?: string;
  optionTestid?: (v: T) => string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] shrink-0">{label}</span>
      <div
        role="tablist"
        aria-label={label}
        data-testid={testid}
        className="flex items-center gap-0.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5"
      >
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={o.value}
              role="tab"
              aria-selected={active}
              onClick={() => onChange(o.value)}
              data-testid={optionTestid?.(o.value)}
              className={`px-2.5 py-1 text-[11px] font-medium rounded-md transition-colors whitespace-nowrap ${
                active
                  ? 'bg-primary text-white shadow-sm'
                  : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)]'
              }`}
            >{o.label}</button>
          );
        })}
      </div>
    </div>
  );
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
  const [editing, setEditing] = useState<ToDo | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  // A fetch that hits the 1000-row cap is truncated — surface it so the count is
  // never silently wrong (meta-review scaling finding). Honest > silent drop.
  const [truncated, setTruncated] = useState(false);

  const windowDays = RANGES[rangeIdx].days;
  const FETCH_CAP = 1000;

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      // Fetch active + windowed terminal, once. history windowDays is a fetch-breadth
      // hint; the authoritative range filter is client-side on createdAt (below), so
      // table/KPI/charts share one口径. 'All' → no window (broad fetch).
      const [list, hist] = await Promise.all([
        todosService.list('swarmws', undefined, FETCH_CAP),
        todosService.history(FETCH_CAP, windowDays ?? undefined),
      ]);
      // Either leg hitting the cap means the view may be missing older rows.
      setTruncated(list.length >= FETCH_CAP || hist.todos.length >= FETCH_CAP);
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

  const handleSaved = useCallback(() => {
    setCreating(false);
    setEditing(null);
    void refresh();
  }, [refresh]);

  // Manual status change from the detail drawer's dropdown. Routes each target ZONE
  // through its SANCTIONED endpoint — NOT a raw PUT of status. This preserves the
  // backend lifecycle invariants that a bare `update({status})` bypasses:
  //   • Completed → mark-handled  (transition_status, guards terminal re-entry)
  //   • Cancelled → mark-cancelled (same)
  //   • To Do (Pending) → retreat  (clears the dispatch snapshot ②→①)
  // 'In Progress' is intentionally NOT a manual target — it is entered by DISPATCH
  // (drag-to-chat, needs a tab), not a status flip. Terminal zones show no control
  // (see StatusSelect), so this only ever fires for legal forward transitions.
  //
  // Guard on the DERIVED zone (not raw status): a dispatched-but-pending todo derives
  // 'In Progress' while raw status is still 'pending' — comparing raw would fire a
  // spurious write. No rows[] optimistic patch (it made the row vanish under the
  // 'Open' filter mid-write); we refresh from server truth. Terminal transitions
  // close the drawer (matches Withdraw); retreat keeps it open and re-syncs `selected`.
  const handleSetZone = useCallback(async (todo: ToDo, target: TodoStatusLabel) => {
    if (deriveStatus(todo) === target) return;
    setActionErr(null);
    try {
      if (target === 'Completed') {
        await todosService.markHandled(todo.id);
        setSelected((cur) => (cur?.id === todo.id ? null : cur));
      } else if (target === 'Cancelled') {
        await todosService.markCancelled(todo.id);
        setSelected((cur) => (cur?.id === todo.id ? null : cur));
      } else if (target === 'Pending') {
        const updated = await todosService.retreat(todo.id);
        setSelected((cur) => (cur?.id === todo.id ? updated : cur));
      } else {
        return; // 'In Progress' is dispatch-only — not a manual target
      }
      void refresh();
    } catch {
      setActionErr('Could not change status — please try again.');
      void refresh();
    }
  }, [refresh]);

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="todo-overlay">
      {/* FilterBar: range + status chips (left) · New ToDo (right) */}
      <WorkbenchToolbar
        gap={2}
        loading={loading}
        left={(
          // Method B: two matching segmented controls through the SAME <Segmented>
          // primitive (one selected-state rule → no style drift). A 1px vertical rule
          // + wide gap BETWEEN groups, tight gap WITHIN each — spacing hierarchy makes
          // "which group / which All" self-evident (Refactoring UI #9). Distinct
          // bordered containers + kept-visible labels → the two 'All's can't confuse.
          <div className="flex items-center gap-3 flex-wrap">
            <Segmented
              label="Range"
              testid="todo-range"
              options={RANGES.map((r) => ({ value: r.label, label: r.label }))}
              value={RANGES[rangeIdx].label}
              onChange={(v) => setRangeIdx(RANGES.findIndex((r) => r.label === v))}
              optionTestid={(v) => `todo-range-${v}`}
            />
            <span aria-hidden className="h-5 w-px bg-[var(--color-border)] shrink-0" />
            <Segmented
              label="Status"
              testid="todo-status-chips"
              options={STATUS_FILTERS.map((f) => ({ value: f, label: f }))}
              value={statusFilter}
              onChange={setStatusFilter}
              optionTestid={(v) => `todo-chip-${v.replace(/\s+/g, '-')}`}
            />
          </div>
        )}
        right={(
          <button
            onClick={() => { setSelected(null); setCreating(true); }}
            data-testid="todo-new-btn"
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-md bg-primary text-white hover:opacity-90 transition-opacity shrink-0 shadow-sm"
          >
            <span className="material-symbols-outlined text-[16px]">add</span>New ToDo
          </button>
        )}
      />

      <GuideBanner />

      {/* KPI row */}
      <KpiRow kpis={kpis} rangeLabel={RANGES[rangeIdx].label} />

      {/* Analytics strip — always visible */}
      <AnalyticsStrip weekly={weekly} sources={sources} />

      {/* Truncation hint — never silently drop rows (meta-review scaling finding) */}
      {truncated && (
        <div
          data-testid="todo-truncated"
          className="shrink-0 mx-4 mt-2 text-[11px] text-[var(--color-text-faint)] flex items-center gap-1.5"
        >
          <span className="material-symbols-outlined text-[13px]">info</span>
          Showing the most recent {FETCH_CAP} ToDos — narrow the time range to see a focused set.
        </div>
      )}

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

      {selected && (
        <DetailDrawer
          todo={selected}
          onClose={() => setSelected(null)}
          onEdit={() => { setEditing(selected); setSelected(null); }}
          onDispatch={handleDispatch}
          onWithdraw={(t) => { void handleWithdraw(t); }}
          onSetZone={(t, z) => { void handleSetZone(t, z); }}
        />
      )}
      {creating && <TodoForm mode="create" onSaved={handleSaved} onCancel={() => setCreating(false)} />}
      {editing && <TodoForm mode="edit" initial={editing} onSaved={handleSaved} onCancel={() => setEditing(null)} />}
    </div>
  );
}

// ── KPI row ──────────────────────────────────────────────────────────

function KpiRow({ kpis, rangeLabel }: { kpis: ReturnType<typeof computeKpis>; rangeLabel: string }) {
  const pct = Math.round(kpis.completionRate * 100);
  // The KPI row is the PRIMARY scannable layer — big numbers, the one thing that
  // stands out (Von Restorff). 'Open' is the actionable headline, emphasized; the
  // rest are context. Grid (not flex-gap) so it shares the analytics strip's
  // column rhythm below → no more misalignment (Gate-1 F4).
  return (
    <div className="shrink-0 grid grid-cols-4 gap-3 px-4 py-3 border-b border-[var(--color-border)]" data-testid="todo-kpis">
      <Kpi label="Open" value={kpis.open} testid="kpi-open" icon="pending_actions" primary />
      <Kpi label="In Progress" value={kpis.inProgress} testid="kpi-inprogress" icon="autorenew" />
      <Kpi label={`Completed · ${rangeLabel}`} value={kpis.completed} testid="kpi-completed" icon="task_alt" />
      <Kpi label="Completion" value={`${pct}%`} testid="kpi-rate" icon="donut_large" />
    </div>
  );
}

function Kpi({ label, value, testid, icon, primary }: { label: string; value: number | string; testid: string; icon: string; primary?: boolean }) {
  // Each KPI is now a bordered card (subtle bg lift) with a leading icon in a rounded
  // tile — a coherent "stat card" language shared with the rest of the surface. The
  // primary card (Open = the actionable headline) gets the accent border + tinted bg
  // so exactly ONE card dominates (Von Restorff); hierarchy via color+weight, not size.
  return (
    <div
      data-testid={testid}
      className={`flex items-center gap-2.5 rounded-lg border px-3 py-2.5 ${
        primary
          ? 'border-primary/30 bg-primary/[0.07]'
          : 'border-[var(--color-border)] bg-[var(--color-bg)]/40'
      }`}
    >
      <span
        className={`material-symbols-outlined text-[18px] shrink-0 ${primary ? 'text-primary' : 'text-[var(--color-text-muted)]'}`}
      >{icon}</span>
      <div className="flex flex-col min-w-0">
        <span className={`text-[20px] leading-none tabular-nums ${primary ? 'font-bold text-primary' : 'font-semibold text-[var(--color-text)]'}`}>{value}</span>
        <span className="mt-1 text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)] truncate">{label}</span>
      </div>
    </div>
  );
}

// ── Analytics strip (always visible) ─────────────────────────────────

function AnalyticsStrip({ weekly, sources }: {
  weekly: ReturnType<typeof weeklyBuckets>;
  sources: ReturnType<typeof sourceDist>;
}) {
  // Secondary layer: same px-4 + 2-col grid as KpiRow (shared rhythm → aligned,
  // not offset). Headers are deliberately muted/smaller than the KPI numbers so
  // this reads as supporting context, not a competing headline (Von Restorff:
  // only ONE thing dominant — the KPIs above).
  return (
    <div className="shrink-0 grid grid-cols-2 gap-3 px-4 py-3 border-b border-[var(--color-border)]" data-testid="todo-analytics">
      {/* Each chart lives in a matching card (same border/radius/bg as the KPI cards
          above) so the whole surface reads as one system, not stacked ad-hoc rows. */}
      <div className="flex flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]/40 px-3 py-2.5" data-testid="todo-weekly">
        <div className="flex items-center justify-between">
          <div className="text-[9px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Weekly activity</div>
          {/* Legend — was missing entirely; gray vs accent bars were unlabeled. */}
          <div className="flex items-center gap-2.5 text-[9px] text-[var(--color-text-muted)]">
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-[var(--color-text-faint)]/40" />Created</span>
            <span className="inline-flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-primary" />Completed</span>
          </div>
        </div>
        <WeeklyBars data={weekly} />
      </div>
      <div className="flex flex-col gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]/40 px-3 py-2.5" data-testid="todo-sources">
        <div className="text-[9px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Source distribution</div>
        <SourceBars data={sources} />
      </div>
    </div>
  );
}

function WeeklyBars({ data }: { data: ReturnType<typeof weeklyBuckets> }) {
  if (data.length === 0) return <div className="text-[11px] text-[var(--color-text-faint)] py-4 text-center">No activity in range</div>;
  const max = Math.max(1, ...data.map((d) => Math.max(d.created, d.completed)));
  return (
    <div className="flex items-end gap-1.5 h-16">
      {data.slice(-10).map((d) => (
        <div key={d.week} className="flex-1 flex flex-col justify-end gap-0.5 group" title={`${d.week}: ${d.created} created / ${d.completed} completed`}>
          <div className="flex items-end gap-0.5 h-full">
            <div className="flex-1 bg-[var(--color-text-faint)]/40 rounded-sm self-end transition-opacity group-hover:opacity-80" style={{ height: `${(d.created / max) * 100}%` }} />
            <div className="flex-1 bg-primary rounded-sm self-end transition-opacity group-hover:opacity-80" style={{ height: `${(d.completed / max) * 100}%` }} />
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
          <thead className="sticky top-0 bg-[var(--color-card)] z-[1]">
            <tr className="border-b border-[var(--color-border)]">
              {COLUMNS.map((c) => {
                const active = sortKey === c.key;
                return (
                  <th
                    key={c.key}
                    onClick={() => onSort(c.key)}
                    data-testid={`todo-th-${c.key}`}
                    className={`text-left px-3 py-2 text-[10px] font-mono uppercase tracking-wider cursor-pointer select-none whitespace-nowrap transition-colors ${
                      active ? 'text-primary' : 'text-[var(--color-text-faint)] hover:text-[var(--color-text)]'
                    }`}
                  >
                    {c.label}
                    <span className="ml-1 text-[9px]">{active ? (sortDir === 'asc' ? '▲' : '▼') : ''}</span>
                  </th>
                );
              })}
              <th className="text-right px-3 py-2 text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">Actions</th>
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
  const terminal = status === 'Completed' || status === 'Cancelled';
  const priColor = PRIORITY_COLOR[todo.priority];
  return (
    <tr
      className="group border-b border-[var(--color-border)]/60 text-[var(--color-text)] hover:bg-[var(--color-hover)] cursor-pointer transition-colors"
      data-testid="todo-row"
      onClick={() => onSelect(todo)}
    >
      {/* Priority — colored dot + label; the dot doubles as a left status accent. */}
      <td className="px-3 py-2.5">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: priColor === 'transparent' ? 'var(--color-border)' : priColor }} />
          <span className="text-[11px] text-[var(--color-text-muted)] capitalize">{todo.priority === 'none' ? '—' : todo.priority}</span>
        </span>
      </td>
      <td className="px-3 py-2.5 max-w-[320px]">
        <span className="block truncate font-medium text-[var(--color-text)]">{todo.title}</span>
      </td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-muted)]">{todo.sourceType}</td>
      <td className="px-3 py-2.5"><StatusBadge status={status} /></td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.createdAt)}</td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.updatedAt)}</td>
      <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(todo.completedAt)}</td>
      {/* Actions must not open the drawer. Quiet until row-hover (opacity) so the
          table scans clean; the primary Dispatch stays accent-colored on reveal. */}
      <td className="px-3 py-2.5 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
        <div className="inline-flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          {!terminal && (
            <RowBtn onClick={() => onDispatch(todo)} icon="play_arrow" label="Dispatch" primary testid="todo-action-dispatch" />
          )}
          <RowBtn onClick={() => onWithdraw(todo)} icon="delete" label="Withdraw" testid="todo-action-withdraw" />
        </div>
      </td>
    </tr>
  );
}

/** Status badge — colored dot + label so status is legible at a glance and color is
 *  never the SOLE signal (accessibility). Shared by the table AND the detail header. */
function StatusBadge({ status }: { status: TodoStatusLabel }) {
  const cls: Record<TodoStatusLabel, string> = {
    Pending: 'text-[var(--color-text-muted)] bg-[var(--color-hover)]',
    'In Progress': 'text-amber-400 bg-amber-500/10',
    Completed: 'text-emerald-400 bg-emerald-500/10',
    Cancelled: 'text-red-400 bg-red-500/10',
  };
  const dot: Record<TodoStatusLabel, string> = {
    Pending: 'bg-[var(--color-text-muted)]',
    'In Progress': 'bg-amber-400',
    Completed: 'bg-emerald-400',
    Cancelled: 'bg-red-400',
  };
  return (
    <span className={`inline-flex items-center gap-1.5 text-[10px] font-medium px-2 py-0.5 rounded-full whitespace-nowrap ${cls[status]}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dot[status]}`} />{status}
    </span>
  );
}

/** Status control for the detail drawer — a StatusBadge that becomes a dropdown when
 *  (and only when) legal manual transitions exist. Covers the case the auto-hook
 *  can't: a manual todo with no bound session / no linked files never auto-completes,
 *  so a human close needs a direct control.
 *
 *  Operates in the derived-ZONE domain (the label the user sees), and offers ONLY
 *  targets that map to a SANCTIONED backend endpoint from the current zone:
 *    • To Do / In Progress → Completed | Cancelled  (mark-handled / mark-cancelled)
 *    • In Progress → (also) To Do                    (retreat — clears dispatch)
 *  Terminal zones (Completed, Cancelled) expose NO control — they render as a plain
 *  read-only badge. This is deliberate: `transition_status` refuses terminal→X on the
 *  backend, so a dropdown there would be a dead control that silently no-ops (the exact
 *  reviewed-todo failure the adversarial review caught). 'In Progress' is never a
 *  manual TARGET — it is reached by Dispatch (drag-to-chat), not a status flip. */
const ZONE_TARGETS: Record<TodoStatusLabel, TodoStatusLabel[]> = {
  Pending: ['Completed', 'Cancelled'],
  'In Progress': ['Completed', 'Cancelled', 'Pending'],
  Completed: [],
  Cancelled: [],
};
function StatusSelect({ todo, onSetZone }: {
  todo: ToDo;
  onSetZone: (t: ToDo, target: TodoStatusLabel) => void;
}) {
  const status = deriveStatus(todo);
  const targets = ZONE_TARGETS[status];
  // Terminal (or no legal move) → read-only badge, no dropdown affordance.
  if (targets.length === 0) {
    return <span data-testid="todo-status-readonly"><StatusBadge status={status} /></span>;
  }
  return (
    <span className="relative inline-flex items-center" data-testid="todo-status-select">
      <StatusBadge status={status} />
      <span className="material-symbols-outlined text-[13px] text-[var(--color-text-muted)] -ml-0.5 pointer-events-none">arrow_drop_down</span>
      {/* Native <select> overlaid transparently → real keyboard/a11y + zero-dep menu.
          value pinned to the current zone; options are current + legal targets. The
          onChange only fires onSetZone for a DIFFERENT target (handler also guards). */}
      <select
        aria-label="Change status"
        data-testid="todo-status-select-input"
        value={status}
        onChange={(e) => onSetZone(todo, e.target.value as TodoStatusLabel)}
        onClick={(e) => e.stopPropagation()}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
      >
        <option value={status}>{status}</option>
        {targets.map((t) => (
          <option key={t} value={t}>{t}</option>
        ))}
      </select>
    </span>
  );
}

function RowBtn({ onClick, icon, label, primary, testid }: { onClick: () => void; icon: string; label: string; primary?: boolean; testid: string }) {
  return (
    <button
      onClick={onClick}
      title={label}
      data-testid={testid}
      className={`inline-flex items-center gap-0.5 px-2 py-1 text-[11px] font-medium rounded-md transition-colors ${
        primary ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-red-400'
      }`}
    >
      <span className="material-symbols-outlined text-[14px]">{icon}</span>{label}
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

function DetailDrawer({ todo, onClose, onEdit, onDispatch, onWithdraw, onSetZone }: {
  todo: ToDo;
  onClose: () => void;
  onEdit: () => void;
  onDispatch: (t: ToDo) => void;
  onWithdraw: (t: ToDo) => void;
  onSetZone: (t: ToDo, target: TodoStatusLabel) => void;
}) {
  const wp = parseWorkPacket(todo.linkedContext);
  const priColor = PRIORITY_COLOR[todo.priority] === 'transparent' ? 'var(--color-text-faint)' : PRIORITY_COLOR[todo.priority];
  const status = deriveStatus(todo);
  const terminal = status === 'Completed' || status === 'Cancelled';
  // Attachments are fetched lazily when the drawer opens (metadata only).
  const [attachments, setAttachments] = useState<ToDoAttachment[]>([]);
  useEffect(() => {
    let alive = true;
    void todosService.listAttachments(todo.id).then((a) => { if (alive) setAttachments(a); }).catch(() => {});
    return () => { alive = false; };
  }, [todo.id]);
  return (
    <OverlayDrawer widthPx={440} maxWidthPct={70} z={10} testid="todo-detail-drawer">
      {/* Header: title is the headline; status is a first-class COLORED badge (was
          buried in a gray "·"-joined line). Priority pill replaces the redundant
          lowercase text; source stays quiet. Hierarchy: title → status → meta. */}
      <div className="px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <div className="flex items-start gap-2.5">
          <span className="mt-0.5 w-1 h-5 rounded-full shrink-0" style={{ background: priColor }} />
          <div className="flex-1 min-w-0 text-[14px] font-semibold text-[var(--color-text)] leading-snug break-words">{todo.title}</div>
          <button onClick={onEdit} data-testid="todo-drawer-edit" title="Edit" className="text-[var(--color-text-muted)] hover:text-primary shrink-0">
            <span className="material-symbols-outlined text-[18px]">edit</span>
          </button>
          <button onClick={onClose} data-testid="todo-drawer-close" title="Close" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] shrink-0">
            <span className="material-symbols-outlined text-[18px]">close</span>
          </button>
        </div>
        <div className="mt-2 flex items-center gap-2 flex-wrap pl-3.5">
          <StatusSelect todo={todo} onSetZone={onSetZone} />
          <PriorityPill priority={todo.priority} />
          <span className="text-[10px] font-mono text-[var(--color-text-faint)]">{todo.sourceType}</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3.5 flex flex-col gap-4 text-[12px]">
        {/* Next step — THE thing you open a todo to see; promoted to a highlighted
            callout above the fold, not a gray peer of Timeline. */}
        {wp?.next_step && (
          <div className="rounded-lg border border-primary/25 bg-primary/[0.06] px-3 py-2.5" data-testid="todo-drawer-nextstep">
            <div className="flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider text-primary">
              <span className="material-symbols-outlined text-[13px]">arrow_forward</span>Next step
            </div>
            <p className="mt-1 text-[12px] text-[var(--color-text)] leading-relaxed break-words">{wp.next_step}</p>
          </div>
        )}

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
            {/* next_step rendered as the callout above — not repeated here. */}
            {wp.acceptance && <Section label="Done when"><p className="text-[var(--color-text)] break-words">{wp.acceptance}</p></Section>}
            <MaterialList label="Files" items={wp.files} mono />
            <MaterialList label="Design docs" items={wp.design_docs} mono />
            <MaterialList label="Commits" items={wp.commits} mono />
            <MaterialList label="Sessions" items={wp.sessions} mono />
            <MaterialList label="Memory refs" items={wp.memory_refs} mono />
            <MaterialList label="Blockers" items={wp.blockers} />
            {wp.notes && <Section label="Notes"><p className="text-[var(--color-text-muted)] break-words whitespace-pre-wrap">{wp.notes}</p></Section>}
          </>
        ) : (
          <div className="text-[11px] text-[var(--color-text-faint)] italic">No work-packet context attached.</div>
        )}

        {attachments.length > 0 && (
          <Section label={`Attachments · ${attachments.length}`}>
            <ul className="flex flex-col gap-1">
              {attachments.map((a) => (
                <li key={a.id} data-testid="todo-detail-attachment">
                  <button
                    type="button"
                    onClick={() => openAttachment(a.relPath)}
                    title={`Open ${a.filename}`}
                    data-testid="todo-detail-attachment-open"
                    className="w-full flex items-center gap-2 text-[11px] text-[var(--color-text)] hover:text-primary hover:bg-[var(--color-hover)] rounded px-1 py-0.5 -mx-1 transition-colors text-left"
                  >
                    <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">attach_file</span>
                    <span className="flex-1 truncate">{a.filename}</span>
                    <span className="font-mono text-[10px] text-[var(--color-text-faint)] tabular-nums">{fmtBytes(a.size)}</span>
                    <span className="material-symbols-outlined text-[13px] text-[var(--color-text-faint)]">open_in_new</span>
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>

      {/* Footer action bar — the drawer's primary CTA. Opening a todo then having to
          close + hunt for the row's Dispatch button was the real gap; Dispatch is now
          the emphasized action right here, Withdraw the quiet destructive secondary. */}
      <div className="flex items-center gap-2 px-4 py-3 border-t border-[var(--color-border)] shrink-0">
        {!terminal ? (
          <button
            onClick={() => onDispatch(todo)}
            data-testid="todo-drawer-dispatch"
            className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-[12px] font-semibold rounded-md bg-primary text-white hover:opacity-90 transition-opacity shadow-sm"
          >
            <span className="material-symbols-outlined text-[16px]">play_arrow</span>Dispatch to chat
          </button>
        ) : (
          <span className="flex-1 text-[11px] text-[var(--color-text-faint)]">This ToDo is {status.toLowerCase()} — no action needed.</span>
        )}
        <button
          onClick={() => onWithdraw(todo)}
          data-testid="todo-drawer-withdraw"
          title="Withdraw"
          className="inline-flex items-center gap-1 px-3 py-2 text-[12px] font-medium rounded-md text-[var(--color-text-muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
        >
          <span className="material-symbols-outlined text-[16px]">delete</span>Withdraw
        </button>
      </div>
    </OverlayDrawer>
  );
}

/** Small colored priority pill for the detail header (replaces redundant text). */
function PriorityPill({ priority }: { priority: Priority }) {
  if (priority === 'none') return null;
  const color = PRIORITY_COLOR[priority];
  return (
    <span
      className="text-[10px] font-medium px-1.5 py-0.5 rounded capitalize"
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >{priority}</span>
  );
}

/** Human-readable byte size. */
function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Open a ToDo attachment in the in-app FileViewer/Canvas. The attachment's
 *  relPath is workspace-relative, so we reuse the SAME `swarm:open-file` event
 *  that explorer/chat file-clicks use (handled by ChatPage's useCanvasHost →
 *  FileViewer, which renders images/PDF/text). This is the sanctioned open path:
 *  a raw `window.open` is silently ignored by the Tauri v2 webview (see
 *  utils/openExternal.ts), so we do NOT use it. No new backend endpoint — the
 *  FileViewer streams from the existing GET /api/workspace/file/raw. */
function openAttachment(relPath: string): void {
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: relPath } }));
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

// ── Unified ToDo form (create + edit) ───────────────────────────────
// One component for BOTH "+ New ToDo" and "Edit" (Detail's pencil). The layout
// is shared so editing a todo reuses the create surface (issue 5). The critical
// safety property (Gate-1 F6): in EDIT mode, save READ-MERGES the existing work
// packet — it only overwrites next_step (the one packet field the form exposes)
// and preserves every other dispatch key (files/commits/design_docs/sessions/
// memory_refs/blockers/acceptance/notes) so a system/dispatched todo never loses
// its context on edit. Attachments are a full-stack feature: uploaded to disk via
// the backend endpoint, listed/removed live. In CREATE mode the todo doesn't
// exist yet, so attachments upload right after create (staged, then flushed).

interface StagedFile { key: string; file: File }

function TodoForm({ mode, initial, onSaved, onCancel }: {
  mode: 'create' | 'edit';
  initial?: ToDo;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const initialWp = useMemo(() => parseWorkPacket(initial?.linkedContext ?? null), [initial]);
  const [title, setTitle] = useState(initial?.title ?? '');
  const [priority, setPriority] = useState<Priority>(initial?.priority ?? 'none');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [nextStep, setNextStep] = useState((initialWp?.next_step as string) ?? '');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Existing (already-uploaded) attachments — edit mode only.
  const [attachments, setAttachments] = useState<ToDoAttachment[]>([]);
  // Staged files in create mode (no todo id yet) — uploaded after create.
  const [staged, setStaged] = useState<StagedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const isEdit = mode === 'edit';

  useEffect(() => {
    if (isEdit && initial) {
      let alive = true;
      void todosService.listAttachments(initial.id).then((a) => { if (alive) setAttachments(a); }).catch(() => {});
      return () => { alive = false; };
    }
  }, [isEdit, initial]);

  const onPickFiles = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setErr(null);
    if (isEdit && initial) {
      // Upload immediately against the existing todo.
      setBusy(true);
      try {
        for (const f of Array.from(files)) {
          const row = await todosService.uploadAttachment(initial.id, f);
          setAttachments((prev) => [row, ...prev]);
        }
      } catch {
        setErr('Attachment upload failed.');
      } finally {
        setBusy(false);
      }
    } else {
      // Create mode: stage for upload-after-create.
      const add = Array.from(files).map((file, i) => ({ key: `${file.name}-${file.size}-${i}-${staged.length}`, file }));
      setStaged((prev) => [...prev, ...add]);
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [isEdit, initial, staged.length]);

  const removeExisting = useCallback(async (att: ToDoAttachment) => {
    if (!initial) return;
    setAttachments((prev) => prev.filter((a) => a.id !== att.id)); // optimistic
    try {
      await todosService.deleteAttachment(initial.id, att.id);
    } catch {
      setErr('Could not remove attachment.');
      void todosService.listAttachments(initial.id).then(setAttachments).catch(() => {});
    }
  }, [initial]);

  const submit = useCallback(async () => {
    const t = title.trim();
    if (!t) { setErr('Title is required.'); return; }
    setBusy(true); setErr(null);
    const ns = nextStep.trim();
    try {
      if (isEdit && initial) {
        // READ-MERGE: overwrite ONLY next_step; preserve all other packet keys.
        const linkedContext = mergeWorkPacket(initial.linkedContext, { next_step: ns });
        await todosService.update(initial.id, {
          title: t,
          priority,
          description: description.trim(),
          linkedContext,
        });
      } else {
        const created = await todosService.create({
          workspaceId: 'swarmws',
          title: t,
          priority,
          sourceType: 'manual',
          description: description.trim() || undefined,
          linkedContext: ns ? JSON.stringify({ next_step: ns }) : undefined,
        });
        // Flush staged attachments now that we have an id. The todo is already
        // created; count failures so we can tell the user which files to re-add
        // instead of silently losing them (Gate-2 MED — no silent data loss).
        let failed = 0;
        for (const s of staged) {
          try { await todosService.uploadAttachment(created.id, s.file); } catch { failed += 1; }
        }
        if (failed > 0) {
          setErr(`ToDo created, but ${failed} attachment${failed > 1 ? 's' : ''} failed to upload — re-add ${failed > 1 ? 'them' : 'it'} via Edit.`);
          setBusy(false);
          // Refresh the list (the todo exists) but keep the drawer open so the
          // message is seen; the user closes it manually.
          return;
        }
      }
      onSaved();
    } catch {
      setErr(isEdit ? 'Failed to save changes.' : 'Failed to create ToDo.');
      setBusy(false);
    }
  }, [isEdit, initial, title, priority, description, nextStep, staged, onSaved]);

  const taClass = 'w-full px-2.5 py-2 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none resize-y';

  return (
    <OverlayDrawer widthPx={520} maxWidthPct={80} z={20} testid={isEdit ? 'todo-edit-form' : 'todo-new-form'}>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="flex-1 text-[13px] font-semibold text-[var(--color-text)]">{isEdit ? 'Edit ToDo' : 'New ToDo'}</span>
        <button onClick={onCancel} data-testid="todo-form-cancel" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3.5 text-[12px]">
        <Field label="Title *">
          <textarea
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            data-testid="todo-form-title"
            rows={2}
            className={taClass}
            placeholder="What needs doing?"
          />
        </Field>
        <Field label="Priority">
          <div className="flex gap-1.5">
            {(['high', 'medium', 'low', 'none'] as Priority[]).map((p) => {
              const active = priority === p;
              const c = PRIORITY_COLOR[p];
              return (
                <button
                  key={p}
                  onClick={() => setPriority(p)}
                  data-testid={`todo-form-pri-${p}`}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-medium capitalize rounded-md border transition-colors ${
                    active ? 'border-primary/50 bg-primary/10 text-primary' : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                  }`}
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: c === 'transparent' ? 'var(--color-border)' : c }} />
                  {p}
                </button>
              );
            })}
          </div>
        </Field>
        <Field label="Description — the detailed context">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            data-testid="todo-form-desc"
            rows={8}
            className={taClass}
            placeholder="Why this exists, background, links, everything the executor needs to start cold…"
          />
        </Field>
        <Field label="Next step">
          <textarea
            value={nextStep}
            onChange={(e) => setNextStep(e.target.value)}
            data-testid="todo-form-nextstep"
            rows={3}
            className={taClass}
            placeholder="Concrete first action(s)"
          />
        </Field>

        {/* Attachments — screenshots + any file, no size/type limit */}
        <Field label="Attachments — screenshots, files, anything">
          <div className="flex flex-col gap-2" data-testid="todo-form-attachments">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              onChange={(e) => void onPickFiles(e.target.files)}
              className="hidden"
              data-testid="todo-form-file-input"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy}
              data-testid="todo-form-attach-btn"
              className="self-start inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium rounded-md border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-primary/50 hover:text-primary transition-colors disabled:opacity-50"
            >
              <span className="material-symbols-outlined text-[15px]">upload_file</span>
              Add files
            </button>
            {(attachments.length > 0 || staged.length > 0) && (
              <ul className="flex flex-col gap-1">
                {attachments.map((a) => (
                  <li key={a.id} className="flex items-center gap-2 px-2 py-1 rounded bg-[var(--color-bg)] text-[11px]" data-testid="todo-form-attachment">
                    <button
                      type="button"
                      onClick={() => openAttachment(a.relPath)}
                      title={`Open ${a.filename}`}
                      data-testid="todo-form-attachment-open"
                      className="flex-1 min-w-0 flex items-center gap-2 hover:text-primary transition-colors text-left"
                    >
                      <span className="material-symbols-outlined text-[14px] text-[var(--color-text-muted)]">attach_file</span>
                      <span className="flex-1 truncate text-[var(--color-text)]">{a.filename}</span>
                      <span className="font-mono text-[10px] text-[var(--color-text-faint)] tabular-nums">{fmtBytes(a.size)}</span>
                    </button>
                    <button onClick={() => void removeExisting(a)} title="Remove" className="text-[var(--color-text-muted)] hover:text-red-400 shrink-0">
                      <span className="material-symbols-outlined text-[14px]">close</span>
                    </button>
                  </li>
                ))}
                {staged.map((s) => (
                  <li key={s.key} className="flex items-center gap-2 px-2 py-1 rounded bg-[var(--color-bg)] text-[11px]" data-testid="todo-form-staged">
                    <span className="material-symbols-outlined text-[14px] text-[var(--color-text-faint)]">schedule</span>
                    <span className="flex-1 truncate text-[var(--color-text-muted)]">{s.file.name}</span>
                    <span className="font-mono text-[10px] text-[var(--color-text-faint)] tabular-nums">{fmtBytes(s.file.size)}</span>
                    <button onClick={() => setStaged((prev) => prev.filter((x) => x.key !== s.key))} title="Remove" className="text-[var(--color-text-muted)] hover:text-red-400">
                      <span className="material-symbols-outlined text-[14px]">close</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {!isEdit && staged.length > 0 && (
              <span className="text-[10px] text-[var(--color-text-faint)]">Files upload when you create the ToDo.</span>
            )}
          </div>
        </Field>

        {err && <div className="text-[11px] text-red-400" data-testid="todo-form-err">{err}</div>}
      </div>
      <div className="flex items-center gap-2 px-4 py-3 border-t border-[var(--color-border)] shrink-0">
        <button
          onClick={submit}
          disabled={busy}
          data-testid="todo-form-submit"
          className="flex-1 px-3 py-1.5 text-[12px] font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 disabled:opacity-50 transition-colors"
        >{busy ? (isEdit ? 'Saving…' : 'Creating…') : (isEdit ? 'Save changes' : 'Create ToDo')}</button>
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
