import { invoke } from '@tauri-apps/api/core';
import { listen, UnlistenFn } from '@tauri-apps/api/event';

export interface BackendStatus {
  running: boolean;
  port: number;
}

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

  // System dependencies check
  async checkNodejsVersion(): Promise<string> {
    return invoke<string>('check_nodejs_version');
  },

  async checkPythonVersion(): Promise<string> {
    return invoke<string>('check_python_version');
  },
};

// Initialize backend connection
export async function initializeBackend(): Promise<number> {
  try {
    // First check if backend is already running
    const status = await tauriService.getBackendStatus();
    if (status.running) {
      setBackendPort(status.port);
      return status.port;
    }

    // Start the backend
    const port = await tauriService.startBackend();
    return port;
  } catch (error) {
    console.error('Failed to initialize backend:', error);
    // Fallback to default port
    return 8000;
  }
}
