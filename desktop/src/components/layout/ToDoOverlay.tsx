/**
 * ToDoOverlay — the left-nav "ToDo" surface: a flow-closure workbench.
 *
 * Opens on `swarm:show-todo` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). Two views inside the fullscreen Modal (Flow | History):
 *   • FLOW    — a 4-zone board (① To Do / ② In Progress / ③ Completed / ④ Recent)
 *               derived from (status, review_state, dispatched_*) by todoZones.
 *   • HISTORY — a read-only table (absolute timestamps) + 5 stat charts.
 *
 * Actions:
 *   Dispatch ①→②  — delegated to `onDispatch` (ChatPage owns tab landing +
 *                    inject + snapshot). Returns true if it landed → overlay
 *                    auto-closes via 2×rAF (so React commits the tab switch
 *                    before the injected input is focused — Gate-1 focus-trap fix).
 *   ↩ Retreat ②→① — todosService via a small backend endpoint (clears snapshot).
 *   ✓Confirm/✗Reject ③ — todosService.review.
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety,
 * mirrors HistoryOverlay).
 *
 * @exports ToDoOverlay
 */
import { useCallback, useEffect, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { todosService, type ToDoHistoryStats } from '../../services/todos';
import type { ToDo } from '../../types/todo';
import { deriveZones, type ZonedTodos } from './todoZones';

export interface ToDoOverlayProps {
  /** Land a todo into a chat tab (inject + snapshot). Returns true if it landed
   *  (→ overlay auto-closes) or false on needs-close (→ overlay stays open). */
  onDispatch: (todo: ToDo) => boolean;
}

type ViewMode = 'flow' | 'history';

const EMPTY_ZONES: ZonedTodos = { todo: [], in_progress: [], completed: [], recent: [] };

export function ToDoOverlay({ onDispatch }: ToDoOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-todo');
  const [view, setView] = useState<ViewMode>('flow');
  const [zones, setZones] = useState<ZonedTodos>(EMPTY_ZONES);
  const [stats, setStats] = useState<ToDoHistoryStats | null>(null);
  const [historyRows, setHistoryRows] = useState<ToDo[]>([]);
  const [loading, setLoading] = useState(false);

  const refreshFlow = useCallback(async () => {
    setLoading(true);
    try {
      // Fetch a broad set (all non-terminal + recent) and derive zones client-side.
      const all = await todosService.list(undefined, undefined, 500);
      setZones(deriveZones(all));
    } catch {
      setZones(EMPTY_ZONES);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    setLoading(true);
    try {
      const [h, s] = await Promise.all([todosService.history(500), todosService.historyStats()]);
      setHistoryRows(h.todos);
      setStats(s);
    } catch {
      setHistoryRows([]);
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load on open + when switching views.
  useEffect(() => {
    if (!open) return;
    if (view === 'flow') void refreshFlow();
    else void refreshHistory();
  }, [open, view, refreshFlow, refreshHistory]);

  // Reset to Flow view when the overlay closes.
  useEffect(() => {
    if (!open) setView('flow');
  }, [open]);

  const handleDispatch = useCallback((todo: ToDo) => {
    const landed = onDispatch(todo);
    if (landed) {
      // Close AFTER React commits the tab switch so the injected input focuses
      // (Gate-1 focus-trap fix: 2×rAF).
      requestAnimationFrame(() => requestAnimationFrame(() => close()));
    }
  }, [onDispatch, close]);

  const handleRetreat = useCallback(async (todo: ToDo) => {
    try { await todosService.retreat(todo.id); } catch { /* non-fatal */ }
    void refreshFlow();
  }, [refreshFlow]);

  const handleReview = useCallback(async (todo: ToDo, action: 'confirm' | 'reject') => {
    try { await todosService.review(todo.id, action); } catch { /* non-fatal */ }
    void refreshFlow();
  }, [refreshFlow]);

  return (
    <Modal isOpen={open} onClose={close} title="ToDo" size="fullscreen" mode="TODO" fullscreenWidth="l">
      <div className="flex-1 min-h-0 flex flex-col" data-testid="todo-overlay">
        {/* Header: Flow | History toggle */}
        <div className="flex items-center gap-1 px-4 py-2 border-b border-[var(--color-border)]">
          {(['flow', 'history'] as ViewMode[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              data-testid={`todo-view-${v}`}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                view === v ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {v === 'flow' ? 'Flow' : 'History'}
            </button>
          ))}
          {loading && <span className="ml-2 text-[11px] text-[var(--color-text-faint)]">Loading…</span>}
        </div>

        {view === 'flow' ? (
          <FlowBoard zones={zones} onDispatch={handleDispatch} onRetreat={handleRetreat} onReview={handleReview} />
        ) : (
          <HistoryPane rows={historyRows} stats={stats} />
        )}
      </div>
    </Modal>
  );
}

// ── Flow board: 4 zones ─────────────────────────────────────────────

function FlowBoard({ zones, onDispatch, onRetreat, onReview }: {
  zones: ZonedTodos;
  onDispatch: (t: ToDo) => void;
  onRetreat: (t: ToDo) => void;
  onReview: (t: ToDo, a: 'confirm' | 'reject') => void;
}) {
  return (
    <div className="flex-1 min-h-0 grid grid-cols-4 gap-3 p-4 overflow-hidden" data-testid="todo-flow-board">
      <Zone label="① To Do" count={zones.todo.length} testid="zone-todo">
        {zones.todo.map((t) => (
          <Card key={t.id} todo={t}>
            <ActionBtn onClick={() => onDispatch(t)} icon="play_arrow" label="Dispatch" primary />
          </Card>
        ))}
      </Zone>
      <Zone label="② In Progress" count={zones.in_progress.length} testid="zone-in-progress">
        {zones.in_progress.map((t) => (
          <Card key={t.id} todo={t} tab={t.dispatchedTabLabel}>
            <ActionBtn onClick={() => onRetreat(t)} icon="undo" label="Retreat" />
          </Card>
        ))}
      </Zone>
      <Zone label="③ Completed" count={zones.completed.length} testid="zone-completed" accent>
        {zones.completed.map((t) => (
          <Card key={t.id} todo={t} tab={t.dispatchedTabLabel}>
            <ActionBtn onClick={() => onReview(t, 'confirm')} icon="check" label="Confirm" primary />
            <ActionBtn onClick={() => onReview(t, 'reject')} icon="close" label="Reject" />
          </Card>
        ))}
      </Zone>
      <Zone label="④ Recent" count={zones.recent.length} testid="zone-recent">
        {zones.recent.map((t) => (
          <Card key={t.id} todo={t}>
            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
              t.reviewState === 'confirmed'
                ? 'text-emerald-400 bg-emerald-500/10'
                : 'text-red-400 bg-red-500/10'
            }`}>
              {t.reviewState === 'confirmed' ? (t.reviewKind === 'auto' ? '✓ auto' : '✓ confirmed') : '✗ rejected'}
            </span>
          </Card>
        ))}
      </Zone>
    </div>
  );
}

function Zone({ label, count, testid, accent, children }: {
  label: string; count: number; testid: string; accent?: boolean; children: React.ReactNode;
}) {
  return (
    <div
      className={`flex flex-col min-h-0 rounded-lg border ${accent ? 'border-primary/30' : 'border-[var(--color-border)]'} bg-[var(--color-card)]`}
      data-testid={testid}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--color-border)] shrink-0">
        <span className="text-xs font-semibold text-[var(--color-text)]">{label}</span>
        <span className="text-[11px] font-mono text-[var(--color-text-muted)]" data-testid={`${testid}-count`}>{count}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
        {count === 0
          ? <div className="text-[11px] text-[var(--color-text-faint)] text-center py-4">—</div>
          : children}
      </div>
    </div>
  );
}

function Card({ todo, tab, children }: { todo: ToDo; tab?: string | null; children: React.ReactNode }) {
  const priColor = todo.priority === 'high' ? '#ef4444' : todo.priority === 'medium' ? '#f59e0b' : todo.priority === 'low' ? '#3b82f6' : 'transparent';
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2 flex flex-col gap-1.5" data-testid="todo-card">
      <div className="flex items-start gap-1.5">
        <span className="mt-1 w-1 h-3 rounded-full shrink-0" style={{ background: priColor }} />
        <span className="flex-1 text-[12px] text-[var(--color-text)] leading-snug break-words">{todo.title}</span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] font-mono text-[var(--color-text-faint)]">{todo.sourceType}</span>
        {tab && <span className="text-[10px] font-mono text-[var(--color-text-muted)]">→ {tab} ⟳</span>}
      </div>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

function ActionBtn({ onClick, icon, label, primary }: { onClick: () => void; icon: string; label: string; primary?: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded transition-colors ${
        primary ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
      }`}
      data-testid={`todo-action-${label.toLowerCase()}`}
    >
      <span className="material-symbols-outlined text-[13px]">{icon}</span>{label}
    </button>
  );
}

// ── History pane: table + 5 charts ──────────────────────────────────

function HistoryPane({ rows, stats }: { rows: ToDo[]; stats: ToDoHistoryStats | null }) {
  return (
    <div className="flex-1 min-h-0 flex overflow-hidden" data-testid="todo-history-pane">
      {/* charts */}
      <div className="w-80 shrink-0 border-r border-[var(--color-border)] overflow-y-auto p-3 flex flex-col gap-4" data-testid="todo-stats">
        {!stats ? (
          <div className="text-[11px] text-[var(--color-text-faint)] text-center py-6">No stats</div>
        ) : (
          <>
            <StatBlock label="Throughput (weekly)">
              <ThroughputBars data={stats.throughputWeekly} />
            </StatBlock>
            <StatBlock label="Completion rate">
              <BigPct value={stats.completionRate} />
            </StatBlock>
            <StatBlock label="Source distribution">
              <SourcePie dist={stats.sourceDistribution} />
            </StatBlock>
            <StatBlock label="Confirm vs auto-confirm">
              <TwoBar a={stats.confirmVsAuto.manual} b={stats.confirmVsAuto.auto} aLabel="manual" bLabel="auto" />
            </StatBlock>
            <StatBlock label="Reject rate">
              <BigPct value={stats.rejectRate} danger />
            </StatBlock>
          </>
        )}
      </div>
      {/* table */}
      <div className="flex-1 overflow-y-auto min-w-[280px]" data-testid="todo-history-table">
        {rows.length === 0 ? (
          <div className="text-sm text-[var(--color-text-muted)] text-center py-8">No history</div>
        ) : (
          <table className="w-full text-[11px]">
            <thead className="sticky top-0 bg-[var(--color-card)] text-[var(--color-text-muted)]">
              <tr>
                <th className="text-left px-3 py-2 font-medium">Title</th>
                <th className="text-left px-2 py-2 font-medium">Source</th>
                <th className="text-left px-2 py-2 font-medium">Outcome</th>
                <th className="text-left px-2 py-2 font-medium">Tab</th>
                <th className="text-left px-2 py-2 font-medium">Reviewed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.id} className="border-t border-[var(--color-border)] text-[var(--color-text)]">
                  <td className="px-3 py-1.5 max-w-[240px] truncate">{t.title}</td>
                  <td className="px-2 py-1.5 font-mono text-[var(--color-text-muted)]">{t.sourceType}</td>
                  <td className="px-2 py-1.5">{outcomeLabel(t)}</td>
                  <td className="px-2 py-1.5 font-mono text-[var(--color-text-faint)]">{t.dispatchedTabLabel ?? '—'}</td>
                  <td className="px-2 py-1.5 font-mono text-[var(--color-text-faint)]">{fmtTs(t.reviewedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function outcomeLabel(t: ToDo): string {
  if (t.reviewState === 'confirmed') return t.reviewKind === 'auto' ? '✓ auto-confirmed' : '✓ confirmed';
  if (t.reviewState === 'rejected') return '✗ rejected';
  if (t.reviewState === 'completed') return '⋯ awaiting review';
  return t.status;
}

/** Absolute timestamp (XG: no "1 hour ago"). */
function fmtTs(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function StatBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{label}</div>
      {children}
    </div>
  );
}

function BigPct({ value, danger }: { value: number; danger?: boolean }) {
  return <div className={`text-2xl font-bold ${danger ? 'text-red-400' : 'text-primary'}`}>{Math.round(value * 100)}%</div>;
}

function ThroughputBars({ data }: { data: { week: string; created: number; completed: number }[] }) {
  const max = Math.max(1, ...data.map((d) => Math.max(d.created, d.completed)));
  if (data.length === 0) return <div className="text-[11px] text-[var(--color-text-faint)]">—</div>;
  return (
    <div className="flex items-end gap-1 h-16">
      {data.slice(-8).map((d) => (
        <div key={d.week} className="flex-1 flex flex-col justify-end gap-0.5" title={`${d.week}: ${d.created} created / ${d.completed} completed`}>
          <div className="w-full bg-[var(--color-text-faint)]/40 rounded-sm" style={{ height: `${(d.created / max) * 100}%` }} />
          <div className="w-full bg-primary rounded-sm" style={{ height: `${(d.completed / max) * 100}%` }} />
        </div>
      ))}
    </div>
  );
}

function TwoBar({ a, b, aLabel, bLabel }: { a: number; b: number; aLabel: string; bLabel: string }) {
  const total = Math.max(1, a + b);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex h-3 rounded overflow-hidden bg-[var(--color-bg)]">
        <div className="bg-primary" style={{ width: `${(a / total) * 100}%` }} />
        <div className="bg-[var(--color-text-faint)]/50" style={{ width: `${(b / total) * 100}%` }} />
      </div>
      <div className="flex justify-between text-[10px] text-[var(--color-text-muted)]">
        <span>{aLabel} {a}</span><span>{bLabel} {b}</span>
      </div>
    </div>
  );
}

function SourcePie({ dist }: { dist: Record<string, number> }) {
  const entries = Object.entries(dist);
  const total = Math.max(1, entries.reduce((s, [, n]) => s + n, 0));
  const colors = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#ec4899'];
  return (
    <div className="flex flex-col gap-1">
      {entries.length === 0 ? <div className="text-[11px] text-[var(--color-text-faint)]">—</div> : entries.map(([k, n], i) => (
        <div key={k} className="flex items-center gap-1.5 text-[10px]">
          <span className="w-2 h-2 rounded-full" style={{ background: colors[i % colors.length] }} />
          <span className="flex-1 text-[var(--color-text-muted)]">{k}</span>
          <span className="font-mono text-[var(--color-text-faint)]">{Math.round((n / total) * 100)}%</span>
        </div>
      ))}
    </div>
  );
}
