/**
 * JobsRunsOverlay — the left-nav "Jobs & Runs" workbench. A structural mirror of
 * ToDoOverlay (fullscreen Modal + view toggle + guide banner + absolute detail
 * drawer + inline create form), for the Swarm Job System instead of ToDos.
 *
 * Opens on `swarm:show-jobs` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). Two views inside the fullscreen Modal (Jobs | Runs):
 *   • JOBS — overview stats strip + a roster of scheduled jobs (health dot,
 *            schedule, last-run, category). Click a card → detail drawer.
 *   • RUNS — a reverse-chron timeline merging pipeline runs (real per-run status)
 *            with the selected/loaded job runs. Absolute YYYY-MM-DD HH:MM stamps.
 *
 * DETAIL DRAWER: absolute right-side overlay (z-10, NOT a flex sibling, so the
 * roster never compresses). Fetches GET /api/jobs/{id}/runs → shows schedule/type/
 * total-runs + the job's REAL last-run output body + a recent-runs list with real
 * per-run status/tokens/duration. Action row: Run now (POST /api/jobs/run direct),
 * Pause/Resume/Edit/Delete → routed to chat via `onDispatch` (s_job-manager owns
 * user-jobs.yaml integrity — the overlay never writes yaml directly).
 *
 * WRITES GO THROUGH CHAT (Gate-1 #7): a bare window `swarm:inject-chat-input` from
 * this layout-level overlay would silently no-op if no chat tab is active (the
 * ChatInput listener only sets the ACTIVE tab's input; it doesn't create/switch
 * tabs). So create/pause/edit/delete call `onDispatch(prompt)` — ChatPage lands +
 * activates a chat tab FIRST (mirror of handleDispatchTodo), THEN injects.
 *
 * GUIDE BANNER: persistent (both views), AI-native first — "ask Swarm in chat to
 * schedule a job" (chat sets cron/prompt/tools/budget via s_job-manager), with the
 * minimal 4-field manual form as the explicit fallback.
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety).
 *
 * @exports JobsRunsOverlay
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import { jobsService, type JobRosterRow, type JobsOverview, type JobRunsResult } from '../../services/jobs';
import { pipelinesService, type PipelineRun } from '../../services/pipelines';

export interface JobsRunsOverlayProps {
  /** Hand a prompt to a chat tab (land+activate a tab, THEN inject). Returns true
   *  if it landed (→ overlay auto-closes) or false on needs-close (stays open).
   *  MUST mirror ChatPage.handleDispatchTodo's tab-landing — a bare inject no-ops
   *  with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
}

type ViewMode = 'jobs' | 'runs';

/** Absolute timestamp (XG rule: no "1 hour ago"). Tolerates null/invalid → —. */
function fmtTs(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Health dot color from a roster row: red = failing, grey = disabled, green = ok. */
function healthColor(j: JobRosterRow): string {
  if (!j.enabled) return 'var(--color-text-faint)';
  if (j.consecutiveFailures > 0 || j.lastStatus === 'failed') return '#ef4444';
  if (j.lastStatus === 'never') return 'var(--color-text-faint)';
  return '#10b981';
}

export function JobsRunsOverlay({ onDispatch }: JobsRunsOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-jobs');
  const [view, setView] = useState<ViewMode>('jobs');
  const [roster, setRoster] = useState<JobRosterRow[]>([]);
  const [overview, setOverview] = useState<JobsOverview | null>(null);
  const [pipelines, setPipelines] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(false);
  // Track the selected job by ID (not a snapshot) so the drawer re-derives fresh
  // data after a refresh (F2: a captured row went stale after Run-now).
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Request-generation guard: a stale (slower) refresh must not overwrite a newer
  // one's result (F3: rapid view toggles launch overlapping fetches).
  const genRef = useRef(0);

  const selected = selectedId ? roster.find((j) => j.id === selectedId) ?? null : null;

  const refreshJobs = useCallback(async () => {
    const gen = ++genRef.current;
    setLoading(true);
    try {
      // Settle roster + overview INDEPENDENTLY (F1): a transient /jobs/status blip
      // must not blank a successfully-fetched roster into a false "no jobs" screen.
      const [rRes, oRes] = await Promise.allSettled([jobsService.fetchRoster(), jobsService.fetchOverview()]);
      if (gen !== genRef.current) return;  // a newer refresh superseded this one
      if (rRes.status === 'fulfilled') setRoster(rRes.value);
      setOverview(oRes.status === 'fulfilled' ? oRes.value : null);
    } finally {
      // Only the CURRENT (winning) generation clears the spinner — a superseded
      // loser must not flip loading off while the winner is still in flight, and
      // the finally guarantees the winner always clears it (meta-review MED: an
      // early return before setLoading(false) could otherwise stick the spinner).
      if (gen === genRef.current) setLoading(false);
    }
  }, []);

  const refreshRuns = useCallback(async () => {
    const gen = ++genRef.current;
    setLoading(true);
    try {
      // Runs view merges pipeline runs + each job's latest run — so it needs the
      // roster too, even when the user jumps straight to Runs without visiting
      // Jobs first (otherwise job rows silently never appear). Settle independently.
      const [plRes, rRes] = await Promise.allSettled([pipelinesService.fetchAllPipelines(), jobsService.fetchRoster()]);
      if (gen !== genRef.current) return;
      setPipelines(plRes.status === 'fulfilled' ? plRes.value : []);
      if (rRes.status === 'fulfilled') setRoster(rRes.value);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    if (view === 'jobs') void refreshJobs();
    else void refreshRuns();
  }, [open, view, refreshJobs, refreshRuns]);

  useEffect(() => {
    if (!open) { setView('jobs'); setSelectedId(null); setCreating(false); }
  }, [open]);

  // Route a chat prompt through the tab-landing dispatcher, then close on success.
  const dispatchToChat = useCallback((prompt: string) => {
    const landed = onDispatch(prompt);
    if (landed) requestAnimationFrame(() => requestAnimationFrame(() => close()));
  }, [onDispatch, close]);

  // Returns true on success, false on failure — the drawer surfaces the error
  // (F3: a silent catch left the user with no feedback on a failed run-now).
  const handleRunNow = useCallback(async (job: JobRosterRow): Promise<boolean> => {
    try {
      await jobsService.runJob(job.id);
      void refreshJobs();  // reflect the new last-run/status
      return true;
    } catch {
      return false;
    }
  }, [refreshJobs]);

  const handleCreated = useCallback(() => { setCreating(false); }, []);

  return (
    <Modal isOpen={open} onClose={close} title="Jobs & Runs" size="fullscreen" mode="JOBS" fullscreenWidth="xl">
      <div className="flex-1 min-h-0 flex flex-col relative" data-testid="jobs-overlay">
        {/* Header: Jobs | Runs toggle + New Job */}
        <div className="flex items-center gap-1 px-4 py-2 border-b border-[var(--color-border)]">
          {(['jobs', 'runs'] as ViewMode[]).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              data-testid={`jobs-view-${v}`}
              className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                view === v ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {v === 'jobs' ? 'Jobs' : 'Runs'}
            </button>
          ))}
          {loading && <span className="ml-2 text-[11px] text-[var(--color-text-faint)]">Loading…</span>}
          <div className="flex-1" />
          <button
            onClick={() => { setSelectedId(null); setCreating(true); }}
            data-testid="jobs-new-btn"
            className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            <span className="material-symbols-outlined text-[15px]">add</span>New Job
          </button>
        </div>

        <GuideBanner />

        {view === 'jobs' ? (
          <JobsView roster={roster} overview={overview} onSelect={(j) => { setCreating(false); setSelectedId(j.id); }} />
        ) : (
          <RunsView pipelines={pipelines} roster={roster} />
        )}

        {/* Detail drawer — absolute overlay, never a flex sibling. Mutually
            exclusive with the New Job form (F2): never stack z-10 under z-20. */}
        {selected && !creating && (
          <JobDetailDrawer
            job={selected}
            onClose={() => setSelectedId(null)}
            onRunNow={handleRunNow}
            onDispatch={dispatchToChat}
          />
        )}

        {/* New Job inline form — absolute overlay */}
        {creating && <NewJobForm onDispatch={dispatchToChat} onCreated={handleCreated} onCancel={() => setCreating(false)} />}
      </div>
    </Modal>
  );
}

// ── Guide banner ────────────────────────────────────────────────────

function GuideBanner() {
  return (
    <div
      className="shrink-0 mx-4 mt-3 rounded-lg border border-primary/25 bg-primary/[0.06] px-3.5 py-2.5 flex items-start gap-2.5"
      data-testid="jobs-guide-banner"
    >
      <span className="material-symbols-outlined text-[16px] text-primary mt-0.5 shrink-0">auto_awesome</span>
      <div className="flex-1 min-w-0 text-[11px] leading-relaxed text-[var(--color-text-muted)]">
        <span className="text-[var(--color-text)] font-medium">Just ask Swarm in chat</span> — the AI-native way.
        Say <span className="font-mono text-[var(--color-text)]">“schedule a job to … every weekday 9am”</span> and it
        sets up the cron, prompt, tools, and budget for you (via the job-manager skill). Chat also handles{' '}
        <span className="text-[var(--color-text)]">edits</span> (“pause the stock-analysis job”) and{' '}
        <span className="text-[var(--color-text)]">deletes</span>.
        <span className="text-[var(--color-text-faint)]"> Or add one manually with <span className="text-primary font-medium">+ New Job</span>.</span>
      </div>
    </div>
  );
}

// ── Jobs view: stats strip + roster ─────────────────────────────────

function JobsView({ roster, overview, onSelect }: {
  roster: JobRosterRow[]; overview: JobsOverview | null; onSelect: (j: JobRosterRow) => void;
}) {
  return (
    <div className="flex-1 min-h-0 flex flex-col overflow-hidden" data-testid="jobs-jobs-view">
      {overview && <OverviewStrip o={overview} />}
      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-1.5" data-testid="jobs-roster">
        {roster.length === 0 ? (
          <div className="text-[11px] text-[var(--color-text-faint)] text-center py-8" data-testid="jobs-empty">
            No jobs yet — ask Swarm in chat to schedule one, or <span className="text-primary font-medium">+ New Job</span>.
          </div>
        ) : (
          roster.map((j) => <JobCard key={j.id} job={j} onSelect={onSelect} />)
        )}
      </div>
    </div>
  );
}

function OverviewStrip({ o }: { o: JobsOverview }) {
  const cells: { label: string; value: string; danger?: boolean }[] = [
    { label: 'total', value: String(o.total) },
    { label: 'healthy', value: String(o.healthy) },
    { label: 'failing', value: String(o.failing), danger: o.failing > 0 },
    { label: 'never-run', value: String(o.neverRun) },
    { label: 'monthly spend', value: `$${o.monthlySpendUsd.toFixed(2)}` },
  ];
  return (
    <div className="shrink-0 mx-4 mt-3 grid grid-cols-3 sm:grid-cols-5 gap-2" data-testid="jobs-overview">
      {cells.map((c) => (
        <div key={c.label} className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 py-1.5 flex flex-col">
          <span className={`text-[15px] font-bold ${c.danger ? 'text-red-400' : 'text-[var(--color-text)]'}`}>{c.value}</span>
          <span className="text-[9px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{c.label}</span>
        </div>
      ))}
    </div>
  );
}

function JobCard({ job, onSelect }: { job: JobRosterRow; onSelect: (j: JobRosterRow) => void }) {
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 flex items-center gap-2.5 cursor-pointer hover:border-primary/40 transition-colors"
      data-testid="job-card"
      onClick={() => onSelect(job)}
    >
      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: healthColor(job) }} data-testid="job-health-dot" />
      <span className="flex-1 min-w-0 text-[12.5px] text-[var(--color-text)] truncate">{job.name}</span>
      <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">{job.schedule || '—'}</span>
      <span className="text-[10px] font-mono text-[var(--color-text-muted)] shrink-0 w-[110px] text-right">{fmtTs(job.lastRun)}</span>
      <span className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded shrink-0 ${
        job.source === 'system' ? 'text-[var(--color-text-faint)] bg-[var(--color-hover)]' : 'text-primary bg-primary/10'
      }`}>{job.source}</span>
    </div>
  );
}

// ── Runs view: merged pipeline + job-run timeline ───────────────────

function RunsView({ pipelines, roster }: { pipelines: PipelineRun[]; roster: JobRosterRow[] }) {
  // Merge pipeline runs (real per-run status + updatedAt) with the latest run of
  // each scheduled job (from roster lastRun). Sorted newest-first by timestamp.
  type Row = { key: string; kind: 'pipeline' | 'job'; name: string; status: string; detail: string; ts: string };
  const rows: Row[] = [
    ...pipelines.map((p): Row => ({
      key: `p-${p.id}`, kind: 'pipeline', name: `${p.project}: ${p.requirement}`.slice(0, 80),
      status: p.status, detail: p.progress || p.currentStage, ts: p.updatedAt,
    })),
    ...roster.filter((j) => j.lastRun).map((j): Row => ({
      key: `j-${j.id}`, kind: 'job', name: j.name, status: j.lastStatus, detail: j.schedule, ts: j.lastRun ?? '',
    })),
  // Sort on the parsed INSTANT, not the raw ISO string: pipeline updatedAt carries
  // a local offset (+08:00) while job lastRun is UTC (Z), so a lexical compare would
  // order by wall-clock text, not chronology (off by up to the offset). Date.parse
  // handles both forms; invalid/empty stamps (→ NaN → 0) sort last.
  ].sort((a, b) => (Date.parse(b.ts) || 0) - (Date.parse(a.ts) || 0));

  return (
    <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3" data-testid="jobs-runs-view">
      {rows.length === 0 ? (
        <div className="text-[11px] text-[var(--color-text-faint)] text-center py-8">No runs yet.</div>
      ) : (
        <div className="flex flex-col gap-1">
          {rows.map((r) => (
            <div key={r.key} className="flex items-center gap-2.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5" data-testid="run-row">
              <span className={`text-[9px] font-mono uppercase px-1.5 py-0.5 rounded shrink-0 ${
                r.kind === 'pipeline' ? 'text-violet-400 bg-violet-500/10' : 'text-sky-400 bg-sky-500/10'
              }`}>{r.kind}</span>
              <span className="flex-1 min-w-0 text-[12px] text-[var(--color-text)] truncate">{r.name}</span>
              <span className="text-[10px] font-mono text-[var(--color-text-muted)] shrink-0">{r.detail}</span>
              <StatusBadge status={r.status} />
              <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 w-[110px] text-right">{fmtTs(r.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const ok = status === 'success' || status === 'completed';
  const bad = status === 'failed' || status === 'cancelled' || status === 'abandoned';
  const cls = ok ? 'text-emerald-400 bg-emerald-500/10' : bad ? 'text-red-400 bg-red-500/10' : 'text-[var(--color-text-muted)] bg-[var(--color-hover)]';
  return <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded shrink-0 ${cls}`}>{status}</span>;
}

// ── Detail drawer ───────────────────────────────────────────────────

function JobDetailDrawer({ job, onClose, onRunNow, onDispatch }: {
  job: JobRosterRow;
  onClose: () => void;
  onRunNow: (j: JobRosterRow) => Promise<boolean>;
  onDispatch: (prompt: string) => void;
}) {
  const [runs, setRuns] = useState<JobRunsResult | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [runState, setRunState] = useState<'idle' | 'running' | 'error'>('idle');

  useEffect(() => {
    let alive = true;
    setLoadingRuns(true);
    jobsService.fetchJobRuns(job.id)
      .then((r) => { if (alive) setRuns(r); })
      .catch(() => { if (alive) setRuns(null); })
      .finally(() => { if (alive) setLoadingRuns(false); });
    return () => { alive = false; };
  }, [job.id]);

  const pauseVerb = job.enabled ? 'pause' : 'resume';
  const isSystem = job.source === 'system';

  return (
    <div
      className="absolute inset-y-0 right-0 w-[420px] max-w-[75%] bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl flex flex-col z-10"
      data-testid="job-detail-drawer"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="mt-1 w-2 h-2 rounded-full shrink-0" style={{ background: healthColor(job) }} />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-[var(--color-text)] leading-snug break-words">{job.name}</div>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] font-mono text-[var(--color-text-faint)]">
            <span>{job.type || '—'}</span><span>·</span><span>{job.source}</span><span>·</span><span>{job.lastStatus}</span>
          </div>
        </div>
        <button onClick={onClose} data-testid="job-drawer-close" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-4 text-[12px]">
        <Section label="Schedule">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[11px]">
            <dt className="text-[var(--color-text-faint)]">Cron</dt><dd className="text-[var(--color-text)] break-all">{job.schedule || '—'}</dd>
            <dt className="text-[var(--color-text-faint)]">Total runs</dt><dd className="text-[var(--color-text)]">{job.totalRuns}</dd>
            <dt className="text-[var(--color-text-faint)]">Last run</dt><dd className="text-[var(--color-text)]">{fmtTs(job.lastRun)}</dd>
          </dl>
          {job.lastError && <div className="mt-1 text-[11px] text-red-400 break-words">{job.lastError}</div>}
        </Section>

        <Section label="Last output">
          {loadingRuns ? (
            <div className="text-[11px] text-[var(--color-text-faint)]">Loading…</div>
          ) : runs?.lastOutput ? (
            <pre className="text-[11px] text-[var(--color-text)] whitespace-pre-wrap break-words max-h-64 overflow-y-auto bg-[var(--color-bg)] rounded p-2 border border-[var(--color-border)]">{runs.lastOutput}</pre>
          ) : (
            <div className="text-[11px] text-[var(--color-text-faint)] italic">No output captured.</div>
          )}
        </Section>

        <Section label="Recent runs">
          {runs && runs.recent.length > 0 ? (
            <ul className="flex flex-col gap-0.5">
              {runs.recent.map((r, i) => (
                <li key={i} className="flex items-center gap-2 text-[11px] font-mono">
                  <span className="text-[var(--color-text-faint)] w-[80px]">{r.date}</span>
                  <StatusBadge status={r.status} />
                  <span className="text-[var(--color-text-muted)]">{r.tokens ?? 0} tok</span>
                  <span className="text-[var(--color-text-faint)]">{(r.duration ?? 0).toFixed(1)}s</span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-[11px] text-[var(--color-text-faint)] italic">No run history.</div>
          )}
        </Section>
      </div>

      {/* Action row */}
      {runState === 'error' && (
        <div className="px-4 pt-2 text-[11px] text-red-400 shrink-0" data-testid="job-run-error">
          Run-now failed — check the job's config or logs.
        </div>
      )}
      <div className="flex items-center gap-1.5 px-4 py-3 border-t border-[var(--color-border)] shrink-0 flex-wrap">
        <DrawerBtn icon="play_arrow" label="Run now" primary disabled={!job.enabled || runState === 'running'}
          onClick={async () => {
            setRunState('running');
            const ok = await onRunNow(job);
            setRunState(ok ? 'idle' : 'error');
          }} />
        {!isSystem && (
          <>
            <DrawerBtn icon={job.enabled ? 'pause' : 'play_circle'} label={job.enabled ? 'Pause' : 'Resume'}
              onClick={() => onDispatch(`${pauseVerb} the "${job.name}" job (id: ${job.id})`)} />
            <DrawerBtn icon="edit" label="Edit"
              onClick={() => onDispatch(`edit the "${job.name}" job (id: ${job.id}) — `)} />
            <DrawerBtn icon="delete" label="Delete"
              onClick={() => onDispatch(`delete the "${job.name}" job (id: ${job.id}) — confirm first`)} />
          </>
        )}
      </div>
    </div>
  );
}

function DrawerBtn({ icon, label, primary, disabled, onClick }: {
  icon: string; label: string; primary?: boolean; disabled?: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      data-testid={`job-action-${label.toLowerCase().replace(/\s/g, '-')}`}
      className={`flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded transition-colors disabled:opacity-40 ${
        primary ? 'bg-primary/10 text-primary hover:bg-primary/20' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
      }`}
    >
      <span className="material-symbols-outlined text-[13px]">{icon}</span>{label}
    </button>
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

// ── New Job form (minimal 4 fields → chat inject) ───────────────────

const CRON_TYPES = ['agent_task', 'script'] as const;
type JobType = (typeof CRON_TYPES)[number];

function NewJobForm({ onDispatch, onCreated, onCancel }: {
  onDispatch: (prompt: string) => void; onCreated: () => void; onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [schedule, setSchedule] = useState('');
  const [type, setType] = useState<JobType>('agent_task');
  const [prompt, setPrompt] = useState('');
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(() => {
    const n = name.trim();
    const s = schedule.trim();
    const p = prompt.trim();
    if (!n || !s || !p) { setErr('Name, schedule, and prompt/command are required.'); return; }
    // Hand inline Swarm the FULL context in one structured message + name the create
    // path explicitly, so it creates the job via s_job-manager in one step rather
    // than inferring intent (mirror of ToDo dispatch handing full context to chat).
    // The overlay never writes yaml itself — s_job-manager owns cron/type/tool
    // validation + user-jobs.yaml integrity, and the human confirms before send
    // (dispatch is autoSend:false) and again at the skill's create gate (HITL).
    const bodyLabel = type === 'agent_task' ? 'Prompt (what Swarm should do each run)' : 'Command (shell)';
    onDispatch(
      [
        `Create a new scheduled job for me using the s_job-manager skill. Confirm the details with me, then create it. Here's what I want:`,
        `- Name: ${n}`,
        `- Type: ${type}`,
        `- Schedule (cron): ${s}`,
        `- ${bodyLabel}: ${p}`,
      ].join('\n'),
    );
    onCreated();
  }, [name, schedule, type, prompt, onDispatch, onCreated]);

  return (
    <div
      className="absolute inset-y-0 right-0 w-[420px] max-w-[75%] bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-2xl flex flex-col z-20"
      data-testid="jobs-new-form"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-border)] shrink-0">
        <span className="flex-1 text-[13px] font-semibold text-[var(--color-text)]">New Job</span>
        <button onClick={onCancel} data-testid="jobs-new-cancel" className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
          <span className="material-symbols-outlined text-[18px]">close</span>
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 flex flex-col gap-3 text-[12px]">
        <div className="text-[11px] text-[var(--color-text-muted)] leading-relaxed">
          Fills a chat prompt for Swarm to create the job (cron/type/prompt validated by the job-manager skill).
          For tool allowlists, budgets, or dependency schedules, just describe them in chat.
        </div>
        <FormField label="Name *">
          <input autoFocus value={name} onChange={(e) => setName(e.target.value)} data-testid="jobs-new-name"
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none"
            placeholder="e.g. Weekly deps audit" />
        </FormField>
        <FormField label="Schedule (cron) *">
          <input value={schedule} onChange={(e) => setSchedule(e.target.value)} data-testid="jobs-new-schedule"
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] font-mono focus:border-primary/50 outline-none"
            placeholder="0 9 * * 1-5" />
        </FormField>
        <FormField label="Type">
          <div className="flex gap-1">
            {CRON_TYPES.map((t) => (
              <button key={t} onClick={() => setType(t)} data-testid={`jobs-new-type-${t}`}
                className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${
                  type === t ? 'border-primary/50 bg-primary/10 text-primary' : 'border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
                }`}>{t}</button>
            ))}
          </div>
        </FormField>
        <FormField label={type === 'agent_task' ? 'Prompt *' : 'Command *'}>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} data-testid="jobs-new-prompt" rows={3}
            className="w-full px-2 py-1.5 rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] text-[var(--color-text)] focus:border-primary/50 outline-none resize-none"
            placeholder={type === 'agent_task' ? 'What should Swarm do each run?' : 'Shell command to run'} />
        </FormField>
        {err && <div className="text-[11px] text-red-400" data-testid="jobs-new-err">{err}</div>}
      </div>
      <div className="flex items-center gap-2 px-4 py-3 border-t border-[var(--color-border)] shrink-0">
        <button onClick={submit} data-testid="jobs-new-submit"
          className="flex-1 px-3 py-1.5 text-[12px] font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 transition-colors">
          Create in chat
        </button>
        <button onClick={onCancel} className="px-3 py-1.5 text-[12px] font-medium rounded-md text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors">Cancel</button>
      </div>
    </div>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">{label}</label>
      {children}
    </div>
  );
}
