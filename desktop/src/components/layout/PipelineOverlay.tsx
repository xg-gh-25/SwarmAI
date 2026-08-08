/**
 * PipelineOverlay — the WORK-zone "Pipeline" retro-analytics dashboard. A
 * structural mirror of JobsRunsOverlay (fullscreen Modal + fetch-once + absolute
 * detail drawer), but RETROSPECTIVE, not live: chat is the natural real-time
 * surface for a running pipeline (XG, run_f8494370), so this card gives the
 * system + human an OVERALL cognition of every pipeline run — health, cycle-time,
 * mode, tokens (est-vs-actual), by project/DDD, plus trends — and lets you resume
 * an aborted/paused run.
 *
 * Opens on `swarm:show-pipeline` (via useExclusiveOverlay → single-overlay mux +
 * back-to-chat). ONE screen shows BOTH the global summary + trend AND the
 * by-project grouping (XG: 全局 + by project 都得一眼看到). Click a run → an
 * absolute right-side detail drawer (z-10, not a flex sibling, so the roster never
 * compresses) that fetches GET /api/pipelines/{run_id} → REPORT retro + reflect
 * lessons + per-stage est-vs-actual token bars + related commits.
 *
 * NO LIVE POLLING — fetches the analytics payload ONCE on open (it is a retro
 * surface; a running pipeline's live state lives in its chat tab). Trend window
 * defaults to 30d, toggles to YTD.
 *
 * WRITES GO THROUGH CHAT (Gate-1 #7, same as JobsRunsOverlay): Resume injects a
 * `run-resume` command via onDispatch (land+activate a chat tab FIRST, then
 * inject — a bare window event no-ops with no active tab). Cancel calls the
 * existing PATCH /api/pipelines/{id}/cancel directly (reversible, non-destructive).
 *
 * Local state ONLY — never MessageStore / active-tab mutation (OT01 safety).
 *
 * @exports PipelineOverlay
 */
import { useCallback, useEffect, useState } from 'react';
import api, { classifyLoadError } from '../../services/api';
import {
  pipelinesService,
  type PipelineAnalytics,
  type PipelineProjectGroup,
  type PipelineRunSummary,
  type PipelineRunDetail,
} from '../../services/pipelines';
import { fmtTs, WorkbenchToolbar, OverlayDrawer } from './overlayShell';

export interface PipelineContentProps {
  /** Hand a prompt to a chat tab (land+activate a tab, THEN inject). Returns true
   *  if it landed (→ host closes the overlay) or false on needs-close. MUST mirror
   *  ChatPage's dispatch — a bare inject no-ops with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
  /** Host-owned close (called after a successful dispatch). */
  close: () => void;
}

type Window = '30d' | 'ytd';

function fmtCycle(min: number | null): string {
  if (min == null) return '—';
  if (min < 60) return `${min}m`;
  return `${(min / 60).toFixed(1)}h`;
}

function fmtTokens(n: number): string {
  if (n <= 0) return '—';
  if (n >= 1000) return `${(n / 1000).toFixed(0)}k`;
  return String(n);
}

function isResumable(s: string, pauseKind: string | null): boolean {
  return s === 'abandoned' || (s === 'paused' && pauseKind !== 'crash_residue');
}

// The canonical crash-residue marker (mirrors artifact_cli._CRASH_ZOMBIE_REASON).
// The detail payload omits pause_kind but carries checkpoint_reason, so we
// re-derive the classification the SAME way the backend does — a crash-residue
// paused run is a dead-session zombie, NOT a resumable decision-pause.
const CRASH_ZOMBIE_REASON = 'session_crash_auto_detected';
function detailPauseKind(status: string, checkpointReason: string | null): string | null {
  if (status !== 'paused') return null;
  return checkpointReason === CRASH_ZOMBIE_REASON ? 'crash_residue' : 'decision';
}

/** Open a run's REPORT.md in Canvas. Mirrors the swarmws overlay precedent
 *  (overlaySurfaces.tsx): close the overlay FIRST (so Canvas isn't rendered under
 *  the host, z-index), THEN dispatch swarm:open-file with the workspace-relative
 *  path — useCanvasHost resolves it via /workspace/file/resolve and renders it.
 *  NOTE: no double-rAF here (that's only for chat-inject, which must wait for a tab
 *  to activate); open-file is a synchronous document event. */
function openReportInCanvas(reportPath: string, close: () => void) {
  close();
  document.dispatchEvent(new CustomEvent('swarm:open-file', { detail: { path: reportPath } }));
}

export function PipelineContent({ onDispatch, close }: PipelineContentProps) {
  const [analytics, setAnalytics] = useState<PipelineAnalytics | null>(null);
  const [window, setWindow] = useState<Window>('30d');
  const [loading, setLoading] = useState(false);
  const [loadErr, setLoadErr] = useState<unknown>(null); // B2: fetch failed (was permanent Loading). Stores the error so classifyLoadError can distinguish 4xx contract vs outage.
  const [reloadTick, setReloadTick] = useState(0); // Retry trigger
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PipelineRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // "Running now" = live active runs (NOT window-filtered analytics — a long run
  // created before the window would be missed, and analytics doesn't strip zombies).
  // fetchActivePipelines hits /pipelines?active=true, which excludes terminal
  // zombies stage-based (Gate-1 #2). null until loaded.
  const [runningNow, setRunningNow] = useState<number | null>(null);
  // A (needs-you focus): clicking the "Needs you" stat收窄 the whole view to just
  // needs-you runs. When true, the常驻 top Needs-you区 hides (it would double-show)
  // and the by-project groups are filtered to needs-you rows only.
  const [needsYouFocus, setNeedsYouFocus] = useState(false);

  // Fetch-once-on-mount (host mounts fresh per open) + on window toggle/retry. NO
  // polling — retro surface. The former reset-on-close effect is gone: the host
  // unmounts/remounts, so selectedRunId/detail/window start fresh each open.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadErr(null);
    // B2: a rejected fetch used to leave loading=true forever (permanent
    // "Loading…") — now .catch surfaces an error state with Retry.
    pipelinesService.fetchAnalytics(window)
      .then((a) => { if (!cancelled) { setAnalytics(a); setLoading(false); } })
      .catch((e) => { if (!cancelled) { setLoadErr(e); setLoading(false); } });
    return () => { cancelled = true; };
  }, [window, reloadTick]);

  // "Running now" — live, window-independent. Fetch-once (retro surface); a running
  // count is a light read and doesn't need to track the window toggle.
  useEffect(() => {
    let cancelled = false;
    pipelinesService.fetchActivePipelines()
      .then((runs) => { if (!cancelled) setRunningNow(runs.filter((r) => r.status === 'running').length); })
      .catch(() => { if (!cancelled) setRunningNow(null); }); // read fail → show — (never crash the overlay)
    return () => { cancelled = true; };
  }, [reloadTick]);

  // Open the detail drawer → fetch that run's retrospective.
  const openRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    setDetail(null);
    setDetailLoading(true);
    void pipelinesService.fetchRunDetail(runId).then((d) => {
      setDetail(d);
      setDetailLoading(false);
    });
  }, []);

  const dispatchToChat = useCallback((prompt: string) => {
    const landed = onDispatch(prompt);
    if (landed) requestAnimationFrame(() => requestAnimationFrame(() => close()));
  }, [onDispatch, close]);

  const handleResume = useCallback((run: PipelineRunSummary | PipelineRunDetail, project: string) => {
    // Route through chat (Gate-1 #7) — inject the resume command, user hits enter.
    dispatchToChat(
      `Resume the paused pipeline: run-resume --project ${project} --run-id ${run.id}, ` +
      `then continue with s_autonomous-pipeline --resume --run-id ${run.id} --project ${project}`,
    );
  }, [dispatchToChat]);

  const handleCancel = useCallback(async (runId: string): Promise<boolean> => {
    try {
      await api.patch(`/pipelines/${runId}/cancel`);
      // reflect locally: re-fetch analytics for the current window. (A window
      // toggle mid-flight self-heals — the window effect re-fetches on toggle and
      // cancel is a rare terminal action; not worth a ref-based guard here.)
      const a = await pipelinesService.fetchAnalytics(window);
      setAnalytics(a);
      setSelectedRunId(null);
      setDetail(null);
      return true;
    } catch {
      // B7: was a silent no-op — user clicked Cancel, PATCH failed, nothing
      // happened. Return false so the drawer can surface the failure.
      return false;
    }
  }, [window]);

  const o = analytics?.overall;

  return (
    <div className="flex-1 min-h-0 flex flex-col relative" data-testid="pipeline-overlay">
        {/* Sub-header: label + window toggle (shared WorkbenchToolbar). */}
        <WorkbenchToolbar
          loading={loading}
          left={
            <span className="text-xs font-medium text-[var(--color-text-muted)]">
              {/* AC1: make the window explicit — the toggle IS a time filter. */}
              Showing runs from {window === '30d' ? 'the last 30 days' : 'Jan 1 (year to date)'}
            </span>
          }
          right={(['30d', 'ytd'] as Window[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              data-testid={`pipeline-window-${w}`}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                window === w ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {w === '30d' ? 'Last 30 days' : 'Year to date'}
            </button>
          ))}
        />

        {/* Body: scrollable — overall strip + trend + by-project groups (one screen) */}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4">
          {/* Overall summary strip */}
          {o && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2" data-testid="pipeline-overall">
              <Stat label="Runs" value={String(o.totalRuns)} />
              <Stat label="Completion" value={o.totalRuns ? `${Math.round(o.completionRate * 100)}%` : '—'} />
              <Stat label="Avg cycle" value={fmtCycle(o.avgCycleMin)} />
              <Stat label="Tokens (act)" value={fmtTokens(o.tokensActual)} sub={`est ${fmtTokens(o.tokensEst)}`} />
              {/* AC5(A): Needs you is a clickable FILTER — click → focus the view to
                  just needs-you runs; click again to clear. */}
              <Stat label="Needs you" value={o.abortedCount > 0 ? String(o.abortedCount) : '0'}
                    accent={o.abortedCount > 0}
                    onClick={o.abortedCount > 0 ? () => setNeedsYouFocus((f) => !f) : undefined}
                    active={needsYouFocus}
                    testid="pipeline-needsyou-stat" />
              {/* AC4: replaced the unreadable "Profiles g69 f109…" ciphertext with a
                  useful live metric — how many pipelines are running RIGHT NOW
                  (window-independent, zombie-excluded). */}
              <Stat label="Running now" value={runningNow == null ? '—' : String(runningNow)} />
            </div>
          )}

          {/* Trend sparkline (compact bars — throughput per week) */}
          {analytics && analytics.trend.length > 0 && (
            <div data-testid="pipeline-trend">
              <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Throughput trend</div>
              <div className="flex items-end gap-1 h-12">
                {analytics.trend.map((t) => {
                  const max = Math.max(...analytics.trend.map((x) => x.runs), 1);
                  return (
                    <div key={t.week} className="flex-1 flex flex-col items-center justify-end" title={`${t.week}: ${t.runs} runs, ${t.completed} done`}>
                      <div className="w-full rounded-t bg-primary/40" style={{ height: `${(t.runs / max) * 100}%`, minHeight: 2 }} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Fetch failure (B2) — distinct from "no runs", with Retry. */}
          {!!loadErr && !loading && (
            <div
              data-testid="pipeline-load-error"
              className="mx-auto my-8 max-w-sm rounded-lg border border-dashed border-[color-mix(in_srgb,#d0524a_45%,var(--color-border))] px-4 py-4 text-center"
            >
              <div className="text-sm text-[var(--color-text)]">{classifyLoadError(loadErr, 'pipeline analytics')}</div>
              <button
                data-testid="pipeline-load-retry"
                onClick={() => setReloadTick((t) => t + 1)}
                className="mt-2 rounded-md px-3 py-1 text-xs font-medium text-white"
                style={{ background: '#d0524a' }}
              >
                Retry
              </button>
            </div>
          )}
          {/* AC6(B): pinned cross-project "Needs you" region — the most-painful runs
              (paused-decision + abandoned) surfaced at the TOP so the user never hunts
              through project groups for them. Hidden while A-focus is active (the whole
              list is already needs-you then — would double-show). */}
          {analytics && !needsYouFocus && (() => {
            const needy = analytics.byProject.flatMap((g) =>
              g.runs
                .filter((r) => r.status === 'abandoned' || r.pauseKind === 'decision')
                .map((r) => ({ run: r, project: g.project })));
            if (needy.length === 0) return null;
            return (
              <div className="rounded-md border border-rose-500/40 bg-rose-500/5" data-testid="pipeline-needsyou-region">
                <div className="flex items-center gap-2 px-3 py-2 border-b border-rose-500/30">
                  <span className="material-symbols-outlined text-[16px] text-rose-500">priority_high</span>
                  <span className="text-sm font-medium text-[var(--color-text)]">Needs you</span>
                  <span className="text-[11px] font-mono text-rose-500">{o?.abortedCount ?? needy.length}</span>
                </div>
                <div>
                  {needy.map(({ run, project }) => (
                    <RunRow key={run.id} r={run} onOpenRun={openRun} close={close} showProject={project} />
                  ))}
                </div>
              </div>
            );
          })()}

          {/* By-project groups */}
          {analytics && analytics.byProject.length === 0 && !loading && !loadErr && (
            <div className="text-center py-10 text-sm text-[var(--color-text-faint)]">
              No pipeline runs in this window. Start one by asking Swarm in chat to run a pipeline.
            </div>
          )}
          {analytics?.byProject.map((g, idx) => (
            <ProjectGroup key={g.project} group={g} onOpenRun={openRun} close={close}
                          defaultExpanded={idx === 0} needsYouOnly={needsYouFocus} />
          ))}
        </div>

        {/* Detail drawer — absolute right overlay (roster never compresses) */}
        {selectedRunId && (
          <RunDetailDrawer
            runId={selectedRunId}
            detail={detail}
            loading={detailLoading}
            onClose={() => { setSelectedRunId(null); setDetail(null); }}
            onResume={handleResume}
            onCancel={handleCancel}
            hostClose={close}
          />
        )}
    </div>
  );
}

function Stat({ label, value, sub, accent, onClick, active, testid }: {
  label: string; value: string; sub?: string; accent?: boolean;
  onClick?: () => void; active?: boolean; testid?: string;
}) {
  const base = `rounded-md border px-2.5 py-1.5 text-left w-full ${
    accent ? 'border-rose-500/50 bg-rose-500/10' : 'border-[var(--color-border)] bg-[var(--color-card)]'
  } ${active ? 'ring-2 ring-rose-500/60' : ''} ${onClick ? 'cursor-pointer hover:brightness-110 transition' : ''}`;
  const inner = (
    <>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)] flex items-center gap-1">
        {label}
        {onClick && <span className="material-symbols-outlined text-[12px]">{active ? 'filter_alt_off' : 'filter_alt'}</span>}
      </div>
      <div className={`text-sm font-mono ${accent ? 'text-rose-500' : 'text-[var(--color-text)]'}`}>{value}</div>
      {sub && <div className="text-[10px] text-[var(--color-text-faint)] font-mono">{sub}</div>}
    </>
  );
  return onClick
    ? <button type="button" onClick={onClick} className={base} data-testid={testid} aria-pressed={active}>{inner}</button>
    : <div className={base} data-testid={testid}>{inner}</div>;
}

/** Status → pill {label, dot color, text color}. "Needs you" is DERIVED, not a raw
 *  status: abandoned OR paused-decision → needs you (rose); paused-crash-residue is a
 *  finished zombie → shows "paused" faint; else the raw status. Kept consistent with
 *  statusDot's colors so the pill and any dot never disagree (Gate-1 #5 label table). */
function statusPill(s: string, pauseKind: string | null): { label: string; dot: string; text: string } {
  if (s === 'abandoned' || pauseKind === 'decision') return { label: 'needs you', dot: 'bg-rose-500', text: 'text-rose-500' };
  if (s === 'completed') return { label: 'completed', dot: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' };
  if (s === 'running') return { label: 'running', dot: 'bg-sky-500', text: 'text-sky-600 dark:text-sky-400' };
  if (s === 'paused') return { label: 'paused', dot: 'bg-amber-500', text: 'text-amber-600 dark:text-amber-400' };
  return { label: s || 'unknown', dot: 'bg-[var(--color-text-faint)]', text: 'text-[var(--color-text-faint)]' };
}

/** One run row — shared by the ProjectGroup roster AND the pinned Needs-you region.
 *  Status pill + requirement (hover title) + optional project tag + profile/cycle/token
 *  + a report button (only when reportPath exists) that opens REPORT.md in Canvas. */
function RunRow({ r, onOpenRun, close, showProject }: {
  r: PipelineRunSummary; onOpenRun: (id: string) => void; close: () => void; showProject?: string;
}) {
  const pill = statusPill(r.status, r.pauseKind);
  return (
    <div
      data-testid={`pipeline-run-${r.id}`}
      className="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-[var(--color-hover)] transition-colors border-b border-[var(--color-border)] last:border-b-0"
    >
      {/* Status pill (dot + label) — replaces the bare color dot (AC2) */}
      <span className={`inline-flex items-center gap-1 shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium ${pill.text}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${pill.dot}`} />{pill.label}
      </span>
      {/* Requirement — click opens the detail drawer; title shows the full text (AC7/D) */}
      <button
        type="button"
        onClick={() => onOpenRun(r.id)}
        title={r.requirement || r.id}
        className="text-xs text-[var(--color-text)] truncate flex-1 min-w-0 text-left"
      >
        {showProject && <span className="text-[var(--color-text-faint)]">{showProject} · </span>}
        {r.requirement || r.id}
      </button>
      <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">{r.profile}</span>
      <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 w-12 text-right">{fmtCycle(r.cycleTimeMin)}</span>
      <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 w-16 text-right">
        {fmtTokens(r.tokensActual)}/{fmtTokens(r.tokensEst)}
      </span>
      {/* Report button (AC2) — only when this run has a REPORT.md. Opens it in Canvas. */}
      {r.reportPath ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); openReportInCanvas(r.reportPath as string, close); }}
          data-testid={`pipeline-run-report-${r.id}`}
          title="Open report in Canvas"
          className="material-symbols-outlined text-[15px] text-primary hover:text-primary/80 shrink-0"
        >
          description
        </button>
      ) : (
        <span className="w-[15px] shrink-0" aria-hidden />
      )}
      {isResumable(r.status, r.pauseKind) && (
        <span className="material-symbols-outlined text-[14px] text-rose-500 shrink-0" title="resumable">play_circle</span>
      )}
    </div>
  );
}

function ProjectGroup({ group, onOpenRun, close, defaultExpanded = false, needsYouOnly = false }: {
  group: PipelineProjectGroup; onOpenRun: (id: string) => void; close: () => void;
  defaultExpanded?: boolean; needsYouOnly?: boolean;
}) {
  // Only the first (most-active) group opens by default — with 750 runs in one
  // project, all-expanded was a wall of buttons on open. The header shows the
  // rollup; click to drill in. In needs-you focus, filter rows to needs-you only.
  const rows = needsYouOnly
    ? group.runs.filter((r) => r.status === 'abandoned' || r.pauseKind === 'decision')
    : group.runs;
  // In focus mode, force-expand (the point is to SEE the needy runs) and drop
  // groups that have none.
  const [expanded, setExpanded] = useState(defaultExpanded);
  const isOpen = needsYouOnly ? true : expanded;
  if (needsYouOnly && rows.length === 0) return null;
  return (
    <div className="rounded-md border border-[var(--color-border)]" data-testid={`pipeline-project-${group.project}`}>
      <button
        onClick={() => setExpanded((e) => !e)}
        disabled={needsYouOnly}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors disabled:cursor-default"
      >
        <span className="material-symbols-outlined text-[16px] text-[var(--color-text-faint)]">
          {isOpen ? 'expand_more' : 'chevron_right'}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">{group.project}</span>
        <span className="text-[11px] text-[var(--color-text-faint)] font-mono">
          {group.runCount} runs · {Math.round(group.completionRate * 100)}% · avg {fmtCycle(group.avgCycleMin)}
        </span>
        {group.abortedCount > 0 && (
          <span className="text-[11px] font-mono text-rose-500">· {group.abortedCount} need you</span>
        )}
      </button>
      {isOpen && (
        <div className="border-t border-[var(--color-border)]">
          {rows.map((r) => (
            <RunRow key={r.id} r={r} onOpenRun={onOpenRun} close={close} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunDetailDrawer({
  runId, detail, loading, onClose, onResume, onCancel, hostClose,
}: {
  runId: string;
  detail: PipelineRunDetail | null;
  loading: boolean;
  onClose: () => void;
  onResume: (run: PipelineRunDetail, project: string) => void;
  onCancel: (runId: string) => Promise<boolean>;
  /** Host-owned overlay close — the report button closes the WHOLE overlay (not just
   *  the drawer) before dispatching swarm:open-file, so Canvas isn't under the host. */
  hostClose: () => void;
}) {
  const [cancelErr, setCancelErr] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  return (
    <OverlayDrawer widthPx={420} maxWidthPct={90} z={10} testid="pipeline-run-drawer" stopPropagation={false}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span className="text-xs font-mono text-[var(--color-text-muted)] truncate flex-1">{runId}</span>
        <button onClick={onClose} className="material-symbols-outlined text-[18px] text-[var(--color-text-faint)] hover:text-[var(--color-text)]">close</button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3 space-y-3">
        {loading && <div className="text-xs text-[var(--color-text-faint)]">Loading…</div>}
        {!loading && !detail && <div className="text-xs text-[var(--color-text-faint)]">Run detail not found.</div>}
        {detail && (
          <>
            <div>
              <div className="text-sm text-[var(--color-text)]">{detail.requirement}</div>
              <div className="text-[11px] font-mono text-[var(--color-text-faint)] mt-0.5">
                {detail.project} · {detail.profile} · {detail.status} · {fmtCycle(detail.cycleTimeMin)} · {fmtTs(detail.createdAt)}
              </div>
            </div>

            {detail.checkpointReason && (
              <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-[var(--color-text)]">
                ⏸ {detail.checkpointReason}
              </div>
            )}

            {/* Action row */}
            <div className="flex gap-2">
              {isResumable(detail.status, detailPauseKind(detail.status, detail.checkpointReason)) && (
                <button
                  onClick={() => onResume(detail, detail.project)}
                  data-testid="pipeline-resume-btn"
                  className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 transition-colors"
                >
                  <span className="material-symbols-outlined text-[15px]">play_arrow</span>Resume
                </button>
              )}
              {(detail.status === 'running' || detail.status === 'paused') && (
                <button
                  disabled={cancelling}
                  onClick={async () => {
                    setCancelErr(false);
                    setCancelling(true);
                    const ok = await onCancel(detail.id);
                    // On success the drawer unmounts (onCancel clears selectedRunId/
                    // detail) — only touch local state on the FAILURE path, or we'd
                    // setState on an unmounting component (Gate-2 LOW).
                    if (!ok) { setCancelling(false); setCancelErr(true); }
                  }}
                  data-testid="pipeline-cancel-btn"
                  className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md text-rose-500 hover:bg-rose-500/10 transition-colors disabled:opacity-50"
                >
                  <span className="material-symbols-outlined text-[15px]">cancel</span>{cancelling ? 'Cancelling…' : 'Cancel'}
                </button>
              )}
            </div>
            {cancelErr && (
              <div data-testid="pipeline-cancel-error" className="text-[11px] text-rose-500">
                Could not cancel this run — please try again.
              </div>
            )}

            {/* Est-vs-actual token bars */}
            {detail.stageTokens.length > 0 && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Tokens — est vs actual</div>
                <div className="space-y-1">
                  {detail.stageTokens.map((s) => {
                    const max = Math.max(...detail.stageTokens.flatMap((x) => [x.est, x.actual]), 1);
                    return (
                      <div key={s.stage} className="text-[10px] font-mono">
                        <div className="flex justify-between text-[var(--color-text-faint)]">
                          <span>{s.stage}</span><span>{fmtTokens(s.actual)}/{fmtTokens(s.est)}</span>
                        </div>
                        <div className="relative h-1.5 rounded bg-[var(--color-hover)]">
                          <div className="absolute top-0 left-0 h-full rounded bg-[var(--color-border-strong)]" style={{ width: `${(s.est / max) * 100}%` }} />
                          <div className="absolute top-0 left-0 h-full rounded bg-primary/60" style={{ width: `${(s.actual / max) * 100}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Reflect lessons */}
            {detail.reflectLessons.length > 0 && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Reflect</div>
                <ul className="space-y-1">
                  {detail.reflectLessons.map((l, i) => (
                    <li key={i} className="text-[11px] text-[var(--color-text-muted)] leading-snug">• {l}</li>
                  ))}
                </ul>
              </div>
            )}

            {/* Related commits */}
            {detail.commits.length > 0 && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Commits</div>
                {detail.commits.map((c, i) => (
                  <div key={i} className="text-[10px] font-mono text-[var(--color-text-muted)]">
                    <span className="text-primary">{c.sha}</span> · {c.files.length} file{c.files.length === 1 ? '' : 's'}
                  </div>
                ))}
              </div>
            )}

            {/* REPORT.md → Canvas (AC3): the full retro is big markdown that read as
                a wall of grey text inline. Open it in Canvas (rendered markdown, full
                screen) instead of dumping it here. Button only when a report exists. */}
            {detail.reportPath && (
              <button
                type="button"
                onClick={() => openReportInCanvas(detail.reportPath as string, hostClose)}
                data-testid="pipeline-detail-report-btn"
                className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md bg-primary/15 text-primary hover:bg-primary/25 transition-colors"
              >
                <span className="material-symbols-outlined text-[15px]">description</span>View report in Canvas
              </button>
            )}
          </>
        )}
      </div>
    </OverlayDrawer>
  );
}
