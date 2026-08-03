/**
 * ToDoOverlay — the left-nav "ToDo" surface: a flow-closure workbench.
 *
 * Opens on `swarm:show-todo` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). Two views inside the fullscreen Modal (Flow | History):
 *   • FLOW    — a 4-zone board (① To Do / ② In Progress / ③ Completed / ④ Recent)
 *               derived from (status, review_state, dispatched_*) by todoZones.
 *   • HISTORY — a de-crowded read-only table (Created + Reviewed absolute stamps)
 *               + 5 stat charts. Full detail lives in the drawer, not the row.
 *
 * DETAIL DRAWER (A3): clicking any card (Flow) or row (History) opens an
 * absolute right-side drawer (layered over the board — NOT a flex sibling, so
 * the 4-zone grid never compresses; Gate-1 A3 M2). It parses linked_context
 * OFF the already-loaded todo (no redundant .get round-trip; Gate-1 A3 H2) and
 * renders all 4 timestamps + the work-packet material (files/design_docs/…).
 * JSON parse is guarded (try/catch + object check; Gate-1 A3 M1).
 *
 * GUIDE BANNER (A3.1): a persistent user-guide strip (both views) that leads
 * with the AI-native path — chat handles add / bulk-import / update / report —
 * with manual "+ New ToDo" as the fallback. (Import is agent-orchestrated —
 * read source tool + loop todo_db.py add — not a wired named integration, so the
 * banner says "another tool", not a brand; R16b — don't name an unbuilt path.)
 *
 * NEW TODO (A3): a header "+ New ToDo" opens an inline form → todosService.create.
 * workspace_id is REQUIRED by the ToDoCreate schema (Field(...), no default) — the
 * client MUST send 'swarmws' or FastAPI 422s (Gate-2 A3 C1). next_step is JSON-encoded
 * into linked_context (Gate-1 A3 C1 — ToDoCreateRequest.linkedContext threads it).
 *
 * Actions:
 *   Dispatch ①→②  — delegated to `onDispatch` (ChatPage owns tab landing +
 *                    inject + snapshot). Returns true if it landed → overlay
 *                    auto-closes via 2×rAF.
 *   ↩ Retreat ②→① — todosService.retreat (clears snapshot).
 *   ✓Confirm/✗Reject ③ — todosService.review.
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety).
 *
 * @exports ToDoOverlay
 */
import { useCallback, useEffect, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { todosService, type ToDoHistoryStats } from '../../services/todos';
import type { ToDo, Priority } from '../../types/todo';
import { deriveZones, type ZonedTodos } from './todoZones';

export interface ToDoOverlayProps {
  /** Land a todo into a chat tab (inject + snapshot). Returns true if it landed
   *  (→ overlay auto-closes) or false on needs-close (→ overlay stays open). */
  onDispatch: (todo: ToDo) => boolean;
}

type ViewMode = 'flow' | 'history';

const EMPTY_ZONES: ZonedTodos = { todo: [], in_progress: [], completed: [], recent: [] };

/** Parse a todo's linked_context JSON string into a work-packet object.
 *  Guarded (Gate-1 A3 M1): null / empty / malformed / non-object → null. */
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

export function ToDoOverlay({ onDispatch }: ToDoOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-todo');
  const [view, setView] = useState<ViewMode>('flow');
  const [zones, setZones] = useState<ZonedTodos>(EMPTY_ZONES);
  const [stats, setStats] = useState<ToDoHistoryStats | null>(null);
  const [historyRows, setHistoryRows] = useState<ToDo[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<ToDo | null>(null);
  const [creating, setCreating] = useState(false);
  // Distinguish "fetch failed" from "genuinely empty" (B4) — an empty board on a
  // backend outage used to look identical to having no todos. `actionErr` surfaces
  // a failed mutation (retreat/review) that used to fail silently (B7).
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const refreshFlow = useCallback(async () => {
    setLoading(true);
    try {
      // Fetch a broad set (all non-terminal + recent) and derive zones client-side.
      const all = await todosService.list(undefined, undefined, 500);
      setZones(deriveZones(all));
      setLoadErr(null);
    } catch {
      setZones(EMPTY_ZONES);
      setLoadErr('Could not load ToDos — the backend may be unavailable.');
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
      setLoadErr(null);
    } catch {
      setHistoryRows([]);
      setStats(null);
      setLoadErr('Could not load ToDo history — the backend may be unavailable.');
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

  // Reset transient UI when the overlay closes.
  useEffect(() => {
    if (!open) { setView('flow'); setSelected(null); setCreating(false); }
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
    setActionErr(null);
    try {
      await todosService.retreat(todo.id);
      setSelected(null);
    } catch {
      // B7: was a silent no-op — the card stayed put with no explanation.
      setActionErr('Could not move that ToDo back — please try again.');
    }
    void refreshFlow();
  }, [refreshFlow]);

  const handleReview = useCallback(async (todo: ToDo, action: 'confirm' | 'reject') => {
    setActionErr(null);
    try {
      await todosService.review(todo.id, action);
      setSelected(null);
    } catch {
      // B7: was a silent no-op — user clicked Confirm/Reject and nothing happened.
      setActionErr(`Could not ${action} that ToDo — please try again.`);
    }
    void refreshFlow();
  }, [refreshFlow]);

  const handleCreated = useCallback(() => {
    setCreating(false);
    void refreshFlow();
  }, [refreshFlow]);

  return (
    <Modal isOpen={open} onClose={close} title="ToDo" size="fullscreen" mode="TODO" fullscreenWidth="l">
      <div className="flex-1 min-h-0 flex flex-col relative" data-testid="todo-overlay">
        {/* Header: Flow | History toggle + New ToDo */}
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
          <div className="flex-1" />
          <button
            onClick={() => { setSelected(null); setCreating(true); }}
            data-testid="todo-new-btn"
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            <span className="material-symbols-outlined text-[15px]">add</span>New ToDo
          </button>
        </div>

        {/* Persistent user-guide banner (both views) — AI-native first. */}
        <GuideBanner />

        {/* Fetch failure (B4) — distinct from an empty board, with Retry. */}
        {loadErr && (
          <div
            data-testid="todo-load-error"
            className="mx-4 mt-2 flex items-center gap-2 rounded-md border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-3 py-2 text-[12px] text-[var(--color-text)]"
          >
            <span className="material-symbols-outlined text-[15px] text-[#d0524a]">error</span>
            <span className="flex-1">{loadErr}</span>
            <button
              data-testid="todo-load-retry"
              onClick={() => { view === 'flow' ? void refreshFlow() : void refreshHistory(); }}
              className="rounded px-2 py-0.5 text-[11px] font-medium text-white"
              style={{ background: '#d0524a' }}
            >
              Retry
            </button>
          </div>
        )}
        {/* Failed mutation (B7) — retreat/review no longer fail silently. */}
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

        {view === 'flow' ? (
          <FlowBoard
            zones={zones}
            onDispatch={handleDispatch}
            onRetreat={handleRetreat}
            onReview={handleReview}
            onSelect={setSelected}
          />
        ) : (
          <HistoryPane rows={historyRows} stats={stats} onSelect={setSelected} />
        )}

        {/* Detail drawer — absolute overlay, never a flex sibling (Gate-1 A3 M2) */}
        {selected && <DetailDrawer todo={selected} onClose={() => setSelected(null)} />}

        {/* New ToDo inline form — absolute overlay */}
        {creating && <NewTodoForm onCreated={handleCreated} onCancel={() => setCreating(false)} />}
      </div>
    </Modal>
  );
}

// ── Flow board: 4 zones ─────────────────────────────────────────────

function FlowBoard({ zones, onDispatch, onRetreat, onReview, onSelect }: {
  zones: ZonedTodos;
  onDispatch: (t: ToDo) => void;
  onRetreat: (t: ToDo) => void;
  onReview: (t: ToDo, a: 'confirm' | 'reject') => void;
  onSelect: (t: ToDo) => void;
}) {
  return (
    <div className="flex-1 min-h-0 grid grid-cols-4 gap-3 p-4 overflow-hidden" data-testid="todo-flow-board">
      <Zone label="① To Do" count={zones.todo.length} testid="zone-todo" emptyHint>
        {zones.todo.map((t) => (
          <Card key={t.id} todo={t} onSelect={onSelect}>
            <ActionBtn onClick={() => onDispatch(t)} icon="play_arrow" label="Dispatch" primary />
          </Card>
        ))}
      </Zone>
      <Zone label="② In Progress" count={zones.in_progress.length} testid="zone-in-progress">
        {zones.in_progress.map((t) => (
          <Card key={t.id} todo={t} tab={t.dispatchedTabLabel} onSelect={onSelect}>
            <ActionBtn onClick={() => onRetreat(t)} icon="undo" label="Retreat" />
          </Card>
        ))}
      </Zone>
      <Zone label="③ Completed" count={zones.completed.length} testid="zone-completed" accent>
        {zones.completed.map((t) => (
          <Card key={t.id} todo={t} tab={t.dispatchedTabLabel} onSelect={onSelect}>
            <ActionBtn onClick={() => onReview(t, 'confirm')} icon="check" label="Confirm" primary />
            <ActionBtn onClick={() => onReview(t, 'reject')} icon="close" label="Reject" />
          </Card>
        ))}
      </Zone>
      <Zone label="④ Recent" count={zones.recent.length} testid="zone-recent">
        {zones.recent.map((t) => (
          <Card key={t.id} todo={t} onSelect={onSelect}>
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

/**
 * Persistent user-guide banner — always visible (Flow + History). Teaches the
 * two ways to work with ToDos and deliberately leads with the AI-native path:
 * chat is the primary route (add / bulk-import / update / report all via chat),
 * manual "+ New ToDo" is the fallback. XG: chat window is AI-native — encourage
 * it, and note chat can BULK-import from other task systems.
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

function Zone({ label, count, testid, accent, emptyHint, children }: {
  label: string; count: number; testid: string; accent?: boolean; emptyHint?: boolean; children: React.ReactNode;
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
          ? (emptyHint
              ? <EmptyTodoHint />
              : <div className="text-[11px] text-[var(--color-text-faint)] text-center py-4">—</div>)
          : children}
      </div>
    </div>
  );
}

/** ① To Do empty-state — the full how-to lives in the persistent GuideBanner
 *  above, so this stays a one-liner pointing there (no duplication). */
function EmptyTodoHint() {
  return (
    <div className="text-[11px] text-[var(--color-text-faint)] leading-relaxed px-2 py-6 text-center" data-testid="todo-empty-hint">
      No open ToDos — ask Swarm in chat, or <span className="text-primary font-medium">+ New ToDo</span>.
    </div>
  );
}

function Card({ todo, tab, onSelect, children }: { todo: ToDo; tab?: string | null; onSelect: (t: ToDo) => void; children: React.ReactNode }) {
  const priColor = todo.priority === 'high' ? '#ef4444' : todo.priority === 'medium' ? '#f59e0b' : todo.priority === 'low' ? '#3b82f6' : 'transparent';
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-2 flex flex-col gap-1.5 cursor-pointer hover:border-primary/40 transition-colors"
      data-testid="todo-card"
      onClick={() => onSelect(todo)}
    >
      <div className="flex items-start gap-1.5">
        <span className="mt-1 w-1 h-3 rounded-full shrink-0" style={{ background: priColor }} />
        <span className="flex-1 text-[12px] text-[var(--color-text)] leading-snug break-words">{todo.title}</span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[10px] font-mono text-[var(--color-text-faint)]">{todo.sourceType}</span>
        {tab && <span className="text-[10px] font-mono text-[var(--color-text-muted)]">→ {tab} ⟳</span>}
      </div>
      {/* Actions must not open the drawer — stop click propagation. */}
      <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>{children}</div>
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

// ── Detail drawer ───────────────────────────────────────────────────

function DetailDrawer({ todo, onClose }: { todo: ToDo; onClose: () => void }) {
  const wp = parseWorkPacket(todo.linkedContext);
  const priColor = todo.priority === 'high' ? '#ef4444' : todo.priority === 'medium' ? '#f59e0b' : todo.priority === 'low' ? '#3b82f6' : 'var(--color-text-faint)';
  return (
    <div
      className="absolute inset-y-0 right-0 w-[360px] max-w-[70%] bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl flex flex-col z-10"
      data-testid="todo-detail-drawer"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="mt-1 w-1.5 h-4 rounded-full shrink-0" style={{ background: priColor }} />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-[var(--color-text)] leading-snug break-words">{todo.title}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] font-mono text-[var(--color-text-faint)]">
            <span>{todo.sourceType}</span>
            <span>·</span>
            <span>{todo.priority}</span>
            <span>·</span>
            <span>{outcomeLabel(todo)}</span>
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

        {/* Timestamps — all 4 (fmtTs tolerates null → —) */}
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

        {/* Work packet material */}
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
    </div>
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
        // Field(...)) — FastAPI 422s before the manager's default can fire, so
        // the client MUST send it. Canonical id is lowercase 'swarmws'
        // (workspace_config.id + todo_db.py WORKSPACE_ID). Gate-2 A3 C1.
        workspaceId: 'swarmws',
        title: t,
        priority,
        sourceType: 'manual',
        description: description.trim() || undefined,
        // Encode next_step into the work packet (Gate-1 A3 C1).
        linkedContext: ns ? JSON.stringify({ next_step: ns }) : undefined,
      });
      onCreated();
    } catch {
      setErr('Failed to create ToDo.');
      setBusy(false);
    }
  }, [title, priority, description, nextStep, onCreated]);

  return (
    <div
      className="absolute inset-y-0 right-0 w-[360px] max-w-[70%] bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl flex flex-col z-20"
      data-testid="todo-new-form"
      onClick={(e) => e.stopPropagation()}
    >
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
    </div>
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

// ── History pane: de-crowded table + 5 charts ───────────────────────

function HistoryPane({ rows, stats, onSelect }: { rows: ToDo[]; stats: ToDoHistoryStats | null; onSelect: (t: ToDo) => void }) {
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
      {/* table — de-crowded: 4 columns, roomy rows, click for detail */}
      <div className="flex-1 overflow-y-auto min-w-[280px]" data-testid="todo-history-table">
        {rows.length === 0 ? (
          <div className="text-sm text-[var(--color-text-muted)] text-center py-8">No history</div>
        ) : (
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-[var(--color-card)] text-[var(--color-text-muted)]">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">Title</th>
                <th className="text-left px-3 py-2.5 font-medium">Outcome</th>
                <th className="text-left px-3 py-2.5 font-medium whitespace-nowrap">Created</th>
                <th className="text-left px-3 py-2.5 font-medium whitespace-nowrap">Reviewed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr
                  key={t.id}
                  className="border-t border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-hover)] cursor-pointer"
                  data-testid="todo-history-row"
                  onClick={() => onSelect(t)}
                >
                  <td className="px-4 py-2.5 max-w-[320px] truncate">{t.title}</td>
                  <td className="px-3 py-2.5">{outcomeLabel(t)}</td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(t.createdAt)}</td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-[var(--color-text-faint)] whitespace-nowrap">{fmtTs(t.reviewedAt)}</td>
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
