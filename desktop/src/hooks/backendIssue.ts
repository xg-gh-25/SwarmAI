/**
 * backendIssue — what the UI says when the backend does not answer, and why.
 *
 * The Tauri watchdog's `backend-degraded` / `backend-terminated-restarting` events
 * carry what it knows (`BackendDiag`): whether the backend process is alive,
 * whether its heartbeat thread is still writing, how long the event loop has not
 * scheduled, and the backend's own diagnosis of the stall (core/stall_diagnosis.py:
 * the CPU fully used / low memory / low battery / busy in our code). This module
 * turns that into one short label (header pill) and one sentence (toast, tooltip).
 * Pure; no React.
 *
 * Kinds:
 * - `degraded`        — alive but slow (inputs stay usable)
 * - `not_responding`  — the watchdog gave up on it for now (stopped / paused / stuck)
 * - `unreachable`     — the app's own health checks failed, with no watchdog signal
 * - `slow_restart`    — still not back after the watchdog's long wait
 *
 * @exports BackendDiag, BackendIssue, BackendIssueKind, describeBackendIssue, parseBackendDiag
 */

export interface BackendStall {
  cause?: 'cpu' | 'memory' | 'low_battery' | 'busy' | 'unknown' | string;
  starved?: boolean;
  loadPerCore?: number;
  memPercent?: number;
  memPressure?: 'normal' | 'warn' | 'critical' | string;
  battery?: number;
  discharging?: boolean;
  busyWith?: string;
  topCpu?: Array<{ name: string; cpu: number }>;
}

export interface BackendDiag {
  heartbeatFresh?: boolean;
  loopAge?: number | null;
  /** null: not known (subprocess mode) */
  processAlive?: boolean | null;
  /** launchd still runs the pid that wrote the (now stale) heartbeat: macOS
   *  suspended the process (sleep, very low battery); it resumes on its own */
  suspended?: boolean;
  /** awake seconds the process has looked suspended (past the watchdog's cap the
   *  watchdog stops calling it suspended: it is then treated as stuck) */
  suspendedSecs?: number | null;
  stall?: BackendStall | null;
}

export type BackendIssueKind = 'degraded' | 'not_responding' | 'unreachable' | 'slow_restart';

export interface BackendIssue {
  kind: BackendIssueKind;
  /** the stall cause, or what kind of outage: a stable key for "did it change" */
  cause: string;
  /** a few words, for the header pill */
  short: string;
  /** one sentence, for the toast and the tooltip */
  detail: string;
}

/** The event payload as the watchdog sends it (an older build sent only a port). */
export function parseBackendDiag(payload: unknown): BackendDiag {
  if (!payload || typeof payload !== 'object') return {};
  const p = payload as Record<string, unknown>;
  const stall = p.stall && typeof p.stall === 'object' ? (p.stall as BackendStall) : null;
  return {
    heartbeatFresh: typeof p.heartbeatFresh === 'boolean' ? p.heartbeatFresh : undefined,
    loopAge: typeof p.loopAge === 'number' ? p.loopAge : null,
    processAlive: typeof p.processAlive === 'boolean' ? p.processAlive : null,
    suspended: p.suspended === true,
    suspendedSecs: typeof p.suspendedSecs === 'number' ? p.suspendedSecs : null,
    stall,
  };
}

const secs = (n?: number | null) => (typeof n === 'number' && n >= 1 ? `${Math.round(n)}s` : '');
const where = (busyWith?: string) => (busyWith ? busyWith.replace(/\.py:/, '.py → ') : '');

function slowed(stall: BackendStall | null | undefined, loopAge?: number | null): Omit<BackendIssue, 'kind'> | null {
  if (!stall) return null;
  const still = 'It is still running and will catch up.';
  switch (stall.cause) {
    case 'cpu': {
      const top = (stall.topCpu ?? []).slice(0, 3).map((p) => `${p.name} ${p.cpu}%`).join(', ');
      const load = typeof stall.loadPerCore === 'number' ? `load ${stall.loadPerCore.toFixed(1)} per core` : '';
      const ev = [load, top && `busiest: ${top}`].filter(Boolean).join('; ');
      return { cause: 'cpu', short: 'Slowed: CPU fully used',
        detail: `The backend is slowed down: this computer's CPU is fully used${ev ? ` (${ev})` : ''}. ${still}` };
    }
    case 'memory': {
      const ev = [typeof stall.memPercent === 'number' && `${Math.round(stall.memPercent)}% used`,
        stall.memPressure === 'critical' && 'macOS memory pressure critical'].filter(Boolean).join(', ');
      return { cause: 'memory', short: 'Slowed: low memory',
        detail: `The backend is slowed down: this computer is low on memory${ev ? ` (${ev})` : ''}. ${still}` };
    }
    case 'low_battery':
      return { cause: 'low_battery', short: 'Slowed: low battery',
        detail: `The backend is slowed down: macOS is throttling it on low battery${typeof stall.battery === 'number' ? ` (${stall.battery}%)` : ''}. Plug in the charger.` };
    case 'busy': {
      const t = secs(loopAge);
      return { cause: 'busy', short: 'Backend busy',
        detail: `The backend has been busy${t ? ` for ${t}` : ''}${stall.busyWith ? ` in ${where(stall.busyWith)}` : ''}; it answers when that finishes.` };
    }
    default:
      return null;
  }
}

export function describeBackendIssue(kind: BackendIssueKind, diag: BackendDiag = {}): BackendIssue {
  const t = secs(diag.loopAge);
  if (diag.suspended && (kind === 'degraded' || kind === 'not_responding')) {
    return { kind, cause: 'paused', short: 'Backend paused',
      detail: 'The backend process exists but is not running: macOS suspended it (the Mac slept, or the battery is very low). It resumes on its own.' };
  }
  if (kind === 'degraded') {
    const s = slowed(diag.stall, diag.loopAge);
    if (s) return { kind, ...s };
    return { kind, cause: 'slow', short: 'Reconnecting…',
      detail: `The backend has not answered${t ? ` for ${t}` : ' for a few seconds'}; its process is running.` };
  }
  if (kind === 'not_responding') {
    if (diag.processAlive === false) {
      return { kind, cause: 'stopped', short: 'Backend stopped',
        detail: 'The backend process is not running; macOS restarts it automatically.' };
    }
    if (diag.processAlive === true && diag.heartbeatFresh === false
        && typeof diag.suspendedSecs === 'number' && diag.suspendedSecs >= 60) {
      const min = Math.round(diag.suspendedSecs / 60);
      return { kind, cause: 'stuck', short: 'Backend stuck',
        detail: `The backend process exists but has not run for ${min} min while this Mac was awake, so macOS is not what paused it: it is probably stuck.` };
    }
    if (diag.processAlive === true && diag.heartbeatFresh === false) {
      return { kind, cause: 'paused', short: 'Backend paused',
        detail: 'The backend process exists but is not running: macOS may have suspended it (the Mac slept, or the battery is very low). It resumes on its own.' };
    }
    if (diag.heartbeatFresh) {
      const s = slowed(diag.stall, diag.loopAge);
      if (s && diag.stall?.starved) return { kind, ...s };
      return { kind, cause: 'stuck', short: 'Backend stuck',
        detail: `The backend has not answered${t ? ` for ${t}` : ''}: ${diag.stall?.busyWith ? `stuck in ${where(diag.stall.busyWith)}` : 'its event loop is stuck'}. Its process is still running.` };
    }
    return { kind, cause: 'stopped', short: 'Backend not responding',
      detail: 'The backend is not responding; reconnecting…' };
  }
  if (kind === 'slow_restart') {
    return { kind, cause: 'slow_restart', short: 'Backend not back yet',
      detail: 'The backend is taking longer than expected to restart…' };
  }
  return { kind, cause: 'unreachable', short: 'Backend unreachable',
    detail: 'The app\'s health checks to the backend failed, and there is no other sign that it is running.' };
}
