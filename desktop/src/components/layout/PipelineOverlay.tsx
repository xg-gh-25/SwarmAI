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
import Modal from '../common/Modal';
import { useExclusiveOverlay } from './useExclusiveOverlay';
import api from '../../services/api';
import {
  pipelinesService,
  type PipelineAnalytics,
  type PipelineProjectGroup,
  type PipelineRunSummary,
  type PipelineRunDetail,
} from '../../services/pipelines';

export interface PipelineOverlayProps {
  /** Hand a prompt to a chat tab (land+activate a tab, THEN inject). Returns true
   *  if it landed (→ overlay auto-closes) or false on needs-close. MUST mirror
   *  ChatPage's dispatch — a bare inject no-ops with no active chat tab (Gate-1 #7). */
  onDispatch: (prompt: string) => boolean;
}

type Window = '30d' | 'ytd';

/** Absolute timestamp (XG rule: no "1 hour ago"). Tolerates null/invalid → —. */
function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

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

/** Status → dot color. Aborted/paused-decision draw attention (work-teal accent). */
function statusDot(s: string, pauseKind: string | null): string {
  if (s === 'completed') return 'bg-emerald-500';
  if (s === 'running') return 'bg-sky-500';
  if (s === 'abandoned' || pauseKind === 'decision') return 'bg-rose-500';
  if (s === 'paused') return 'bg-amber-500';
  return 'bg-[var(--color-text-faint)]';
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

export function PipelineOverlay({ onDispatch }: PipelineOverlayProps) {
  const { open, close } = useExclusiveOverlay('swarm:show-pipeline');
  const [analytics, setAnalytics] = useState<PipelineAnalytics | null>(null);
  const [window, setWindow] = useState<Window>('30d');
  const [loading, setLoading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PipelineRunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Fetch-once-on-open (+ on window toggle). NO polling — retro surface.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void pipelinesService.fetchAnalytics(window).then((a) => {
      if (!cancelled) { setAnalytics(a); setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [open, window]);

  useEffect(() => {
    if (!open) { setSelectedRunId(null); setDetail(null); setWindow('30d'); }
  }, [open]);

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

  const handleCancel = useCallback(async (runId: string) => {
    try {
      await api.patch(`/pipelines/${runId}/cancel`);
      // reflect locally: re-fetch analytics for the current window. (A window
      // toggle mid-flight self-heals — the window effect re-fetches on toggle and
      // cancel is a rare terminal action; not worth a ref-based guard here.)
      const a = await pipelinesService.fetchAnalytics(window);
      setAnalytics(a);
      setSelectedRunId(null);
      setDetail(null);
    } catch {
      /* best-effort; overlay stays open */
    }
  }, [window]);

  const o = analytics?.overall;

  return (
    <Modal isOpen={open} onClose={close} title="Pipeline" size="fullscreen" mode="PIPELINE" fullscreenWidth="xl">
      <div className="flex-1 min-h-0 flex flex-col relative" data-testid="pipeline-overlay">
        {/* Header: title + window toggle */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border)]">
          <span className="text-xs font-medium text-[var(--color-text-muted)]">Retro Analytics</span>
          {loading && <span className="text-[11px] text-[var(--color-text-faint)]">Loading…</span>}
          <div className="flex-1" />
          {(['30d', 'ytd'] as Window[]).map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              data-testid={`pipeline-window-${w}`}
              className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                window === w ? 'bg-primary/15 text-primary' : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)]'
              }`}
            >
              {w === '30d' ? '30 days' : 'YTD'}
            </button>
          ))}
        </div>

        {/* Body: scrollable — overall strip + trend + by-project groups (one screen) */}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4">
          {/* Overall summary strip */}
          {o && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2" data-testid="pipeline-overall">
              <Stat label="Runs" value={String(o.totalRuns)} />
              <Stat label="Completion" value={o.totalRuns ? `${Math.round(o.completionRate * 100)}%` : '—'} />
              <Stat label="Avg cycle" value={fmtCycle(o.avgCycleMin)} />
              <Stat label="Tokens (act)" value={fmtTokens(o.tokensActual)} sub={`est ${fmtTokens(o.tokensEst)}`} />
              <Stat label="Needs you" value={o.abortedCount > 0 ? String(o.abortedCount) : '0'}
                    accent={o.abortedCount > 0} />
              <Stat label="Profiles" value={Object.entries(o.profileMix).map(([k, v]) => `${k[0]}${v}`).join(' ') || '—'} />
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

          {/* By-project groups */}
          {analytics && analytics.byProject.length === 0 && !loading && (
            <div className="text-center py-10 text-sm text-[var(--color-text-faint)]">
              No pipeline runs in this window. Start one by asking Swarm in chat to run a pipeline.
            </div>
          )}
          {analytics?.byProject.map((g) => (
            <ProjectGroup key={g.project} group={g} onOpenRun={openRun} />
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
          />
        )}
      </div>
    </Modal>
  );
}

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div className={`rounded-md border px-2.5 py-1.5 ${accent ? 'border-rose-500/50 bg-rose-500/10' : 'border-[var(--color-border)] bg-[var(--color-card)]'}`}>
      <div className="text-[10px] uppercase tracking-wide text-[var(--color-text-faint)]">{label}</div>
      <div className={`text-sm font-mono ${accent ? 'text-rose-500' : 'text-[var(--color-text)]'}`}>{value}</div>
      {sub && <div className="text-[10px] text-[var(--color-text-faint)] font-mono">{sub}</div>}
    </div>
  );
}

function ProjectGroup({ group, onOpenRun }: { group: PipelineProjectGroup; onOpenRun: (id: string) => void }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="rounded-md border border-[var(--color-border)]" data-testid={`pipeline-project-${group.project}`}>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-[var(--color-hover)] transition-colors"
      >
        <span className="material-symbols-outlined text-[16px] text-[var(--color-text-faint)]">
          {expanded ? 'expand_more' : 'chevron_right'}
        </span>
        <span className="text-sm font-medium text-[var(--color-text)]">{group.project}</span>
        <span className="text-[11px] text-[var(--color-text-faint)] font-mono">
          {group.runCount} runs · {Math.round(group.completionRate * 100)}% · avg {fmtCycle(group.avgCycleMin)}
        </span>
        {group.abortedCount > 0 && (
          <span className="text-[11px] font-mono text-rose-500">· {group.abortedCount} need you</span>
        )}
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)]">
          {group.runs.map((r) => (
            <button
              key={r.id}
              onClick={() => onOpenRun(r.id)}
              data-testid={`pipeline-run-${r.id}`}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-[var(--color-hover)] transition-colors border-b border-[var(--color-border)] last:border-b-0"
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(r.status, r.pauseKind)}`} />
              <span className="text-xs text-[var(--color-text)] truncate flex-1 min-w-0">{r.requirement || r.id}</span>
              <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0">{r.profile}</span>
              <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 w-12 text-right">{fmtCycle(r.cycleTimeMin)}</span>
              <span className="text-[10px] font-mono text-[var(--color-text-faint)] shrink-0 w-16 text-right">
                {fmtTokens(r.tokensActual)}/{fmtTokens(r.tokensEst)}
              </span>
              {isResumable(r.status, r.pauseKind) && (
                <span className="material-symbols-outlined text-[14px] text-rose-500 shrink-0" title="resumable">play_circle</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RunDetailDrawer({
  runId, detail, loading, onClose, onResume, onCancel,
}: {
  runId: string;
  detail: PipelineRunDetail | null;
  loading: boolean;
  onClose: () => void;
  onResume: (run: PipelineRunDetail, project: string) => void;
  onCancel: (runId: string) => void;
}) {
  return (
    <div
      className="absolute top-0 right-0 bottom-0 w-[420px] max-w-[90%] z-10 bg-[var(--color-card)] border-l border-[var(--color-border)] shadow-xl flex flex-col"
      data-testid="pipeline-run-drawer"
    >
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
                  onClick={() => onCancel(detail.id)}
                  data-testid="pipeline-cancel-btn"
                  className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-md text-rose-500 hover:bg-rose-500/10 transition-colors"
                >
                  <span className="material-symbols-outlined text-[15px]">cancel</span>Cancel
                </button>
              )}
            </div>

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

            {/* REPORT.md retro */}
            {detail.reportMd && (
              <div>
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-faint)] mb-1">Report</div>
                <pre className="text-[10px] text-[var(--color-text-muted)] whitespace-pre-wrap leading-snug bg-[var(--color-bg)] rounded p-2 max-h-64 overflow-y-auto">{detail.reportMd}</pre>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
