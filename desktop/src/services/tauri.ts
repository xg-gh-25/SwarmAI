import { invoke } from '@tauri-apps/api/core';
import { listen, UnlistenFn } from '@tauri-apps/api/event';

export interface BackendStatus {
  running: boolean;
  port: number;
  is_daemon_mode: boolean;
}

// ---------------------------------------------------------------------------
// Platform detection
// ---------------------------------------------------------------------------

/** True when running inside Tauri desktop shell, false in browser (Hive mode). */
export function isDesktop(): boolean {
  // Tauri 2.x injects `__TAURI_INTERNALS__`.  Older code used `__TAURI__`
  // (Tauri 1.x), which no longer exists unless `withGlobalTauri: true` is
  // set in tauri.conf.json.  Check both so the detection works across
  // versions and any future bundle-internal naming changes.
  const w = window as unknown as Record<string, unknown>;
  const result = !!(w.__TAURI_INTERNALS__ || w.__TAURI__);
  // Log once on first call — critical for diagnosing v1.9.0-class bugs
  // where isDesktop()=false causes all API calls to hit SPA fallback.
  if (!_isDesktopLogged) {
    _isDesktopLogged = true;
    console.log(`[Platform] isDesktop=${result} (__TAURI_INTERNALS__=${!!w.__TAURI_INTERNALS__}, __TAURI__=${!!w.__TAURI__}, protocol=${location.protocol})`);
  }
  return result;
}
let _isDesktopLogged = false;

// Store the backend port globally
// In development mode, always use 8000 (manual python main.py)
// In production, Tauri sidecar will set this dynamically
let _backendPort: number = 8000;

// Check if running in development mode (Vite dev server)
const isDev = import.meta.env.DEV;

export function getBackendPort(): number {
  // In dev mode, always use 8000 for manual backend
  if (isDev) {
    return 8000;
  }
  return _backendPort;
}

/**
 * Get the base URL for API requests.
 *
 * - Desktop (Tauri): http://localhost:{port}
 * - Hive (browser):  same origin (Caddy proxies /api/*)
 * - Dev (Vite):      http://localhost:8000
 *
 * Used by SSE/fetch calls that construct URLs directly (chat, voice, tasks).
 */
export function getApiBaseUrl(): string {
  // Explicit env override (set at build time for Hive)
  if (import.meta.env.VITE_API_URL) {
    if (!_apiBaseLogged) { _apiBaseLogged = true; console.log(`[Platform] API base from VITE_API_URL: ${import.meta.env.VITE_API_URL}`); }
    return import.meta.env.VITE_API_URL;
  }
  // Desktop mode: localhost with dynamic port
  if (isDesktop()) {
    const port = getBackendPort();
    const url = `http://localhost:${port}`;
    if (!_apiBaseLogged) { _apiBaseLogged = true; console.log(`[Platform] API base (desktop): ${url}`); }
    return url;
  }
  // Hive/web mode: same origin (Caddy reverse-proxies /api/*)
  if (!_apiBaseLogged) { _apiBaseLogged = true; console.log(`[Platform] API base (hive/browser): same-origin`); }
  return '';
}
let _apiBaseLogged = false;

export function setBackendPort(port: number): void {
  _backendPort = port;
}

export const tauriService = {
  // Backend management
  async startBackend(): Promise<number> {
    const port = await invoke<number>('start_backend');
    setBackendPort(port);
    return port;
  },

  async stopBackend(): Promise<void> {
    return invoke('stop_backend');
  },

  async getBackendStatus(): Promise<BackendStatus> {
    return invoke<BackendStatus>('get_backend_status');
  },

  async getBackendPortFromTauri(): Promise<number> {
    const port = await invoke<number>('get_backend_port');
    setBackendPort(port);
    return port;
  },

  // Event listeners
  async onBackendLog(callback: (log: string) => void): Promise<UnlistenFn> {
    return listen<string>('backend-log', (event) => callback(event.payload));
  },

  async onBackendError(callback: (error: string) => void): Promise<UnlistenFn> {
    return listen<string>('backend-error', (event) => callback(event.payload));
  },

  async onBackendTerminated(callback: (code: number | null) => void): Promise<UnlistenFn> {
    return listen<number | null>('backend-terminated', (event) => callback(event.payload));
  },

  /** Backend died unexpectedly — auto-restart in progress. */
  async onBackendTerminatedRestarting(callback: (code: number | null) => void): Promise<UnlistenFn> {
    return listen<number | null>('backend-terminated-restarting', (event) => callback(event.payload));
  },

  /** Backend auto-restarted on a new port. */
  async onBackendRestarted(callback: (newPort: number) => void): Promise<UnlistenFn> {
    return listen<number>('backend-restarted', (event) => callback(event.payload));
  },

  /** Backend mode notification: "daemon" or "sidecar". */
  async onBackendMode(callback: (mode: string) => void): Promise<UnlistenFn> {
    return listen<string>('backend-mode', (event) => callback(event.payload));
  },

  // System dependencies check
  async checkNodejsVersion(): Promise<string> {
    return invoke<string>('check_nodejs_version');
  },

  async checkPythonVersion(): Promise<string> {
    return invoke<string>('check_python_version');
  },

  // Check Git Bash path (Windows only)
  async checkGitBashPath(): Promise<string> {
    return invoke<string>('check_git_bash_path');
  },
};

// Initialize backend connection
export async function initializeBackend(): Promise<number> {
  console.log('[Backend Init] Checking if backend is already running...');
  // First check if backend is already running
  const status = await tauriService.getBackendStatus();
  console.log(`[Backend Init] Status: running=${status.running}, port=${status.port}, daemon=${status.is_daemon_mode}`);
  if (status.running) {
    setBackendPort(status.port);
    return status.port;
  }

  // Start the backend — let errors propagate to BackendStartupOverlay
  // which already has error handling UI (L402-408).
  console.log('[Backend Init] Starting backend via Tauri...');
  const port = await tauriService.startBackend();
  console.log(`[Backend Init] Backend started on port ${port}`);
  return port;
}
