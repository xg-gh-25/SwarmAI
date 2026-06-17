/**
 * logForwarder — persists frontend console errors/warnings + uncaught errors
 * to the backend so they land in ~/.swarm-ai/logs/frontend.log.
 *
 * Why: the production Tauri webview console is not written anywhere, so
 * diagnosing UI issues required asking the user to open DevTools. With this,
 * `tail ~/.swarm-ai/logs/frontend.log` shows the same console errors.
 *
 * Design notes:
 * - Patches console.error / console.warn (preserving original behavior) and
 *   captures window 'error' + 'unhandledrejection'.
 * - Batches entries and flushes every few seconds via a RAW fetch — NOT the
 *   axios `api` instance — because api interceptors call console.error, which
 *   would re-enter the forwarder and create an infinite loop.
 * - Fully best-effort: a flush failure is swallowed silently (never console.*,
 *   never throws) so a down backend can't spam or crash the UI.
 *
 * Exports:
 * - `initLogForwarder()` — install once at app startup (idempotent).
 */
import { getApiBaseUrl } from './tauri';

interface Entry {
  level: 'error' | 'warn' | 'log';
  message: string;
  ts: string;
  source?: string;
}

const MAX_QUEUE = 500;
const FLUSH_INTERVAL_MS = 3000;
const MAX_MESSAGE_LEN = 8000;

let queue: Entry[] = [];
let installed = false;
let flushing = false;

function stringifyArg(a: unknown): string {
  if (typeof a === 'string') return a;
  if (a instanceof Error) return `${a.name}: ${a.message}\n${a.stack ?? ''}`;
  try {
    return JSON.stringify(a);
  } catch {
    return String(a);
  }
}

function enqueue(level: Entry['level'], args: unknown[], source?: string): void {
  try {
    const message = args.map(stringifyArg).join(' ').slice(0, MAX_MESSAGE_LEN);
    queue.push({ level, message, ts: new Date().toISOString(), source });
    // Bound memory if the backend is unreachable for a long time.
    if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);
  } catch {
    // The logger must never throw.
  }
}

async function flush(): Promise<void> {
  if (flushing || queue.length === 0) return;
  flushing = true;
  const batch = queue;
  queue = [];
  try {
    // RAW fetch (not the api instance) — avoids interceptors that console.error
    // and would re-enter this forwarder in an infinite loop. Silent on failure.
    await fetch(`${getApiBaseUrl()}/api/system/client-logs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries: batch }),
      keepalive: true,
    });
  } catch {
    // Backend unreachable — drop this batch. Do NOT requeue (would grow
    // unbounded during a long outage) and do NOT console.* (would loop).
  } finally {
    flushing = false;
  }
}

export function initLogForwarder(): void {
  if (installed) return;
  installed = true;

  const origError = console.error.bind(console);
  const origWarn = console.warn.bind(console);

  console.error = (...args: unknown[]) => {
    enqueue('error', args);
    origError(...args);
  };
  console.warn = (...args: unknown[]) => {
    enqueue('warn', args);
    origWarn(...args);
  };

  window.addEventListener('error', (e: ErrorEvent) => {
    enqueue('error', [e.message], `${e.filename}:${e.lineno}:${e.colno}`);
  });

  window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
    const r = e.reason;
    const msg = r instanceof Error ? `${r.name}: ${r.message}\n${r.stack ?? ''}` : String(r);
    enqueue('error', [msg], 'unhandledrejection');
  });

  // Periodic batched flush + a best-effort flush on unload.
  setInterval(() => { void flush(); }, FLUSH_INTERVAL_MS);
  window.addEventListener('beforeunload', () => { void flush(); });
}
