import { check, Update } from '@tauri-apps/plugin-updater';
import { relaunch } from '@tauri-apps/plugin-process';

export interface UpdateProgress {
  downloaded: number;
  total: number | null;
  percentage: number;
}

export interface UpdateInfo {
  version: string;
  date: string | null;
  body: string | null;
}

/**
 * Check for available updates
 * Returns null if no update available or check fails
 */
export async function checkForUpdates(): Promise<Update | null> {
  try {
    const update = await check();
    return update;
  } catch (error) {
    console.error('Failed to check for updates:', error);
    return null;
  }
}

/**
 * Get update info in a friendly format
 */
export function getUpdateInfo(update: Update): UpdateInfo {
  return {
    version: update.version,
    date: update.date ?? null,
    body: update.body ?? null,
  };
}

/**
 * Download and install an update with progress tracking
 */
export async function downloadAndInstallUpdate(
  update: Update,
  onProgress?: (progress: UpdateProgress) => void
): Promise<void> {
  let downloaded = 0;
  let contentLength: number | null = null;

  await update.downloadAndInstall((event) => {
    switch (event.event) {
      case 'Started':
        contentLength = event.data.contentLength ?? null;
        onProgress?.({
          downloaded: 0,
          total: contentLength,
          percentage: 0,
        });
        break;
      case 'Progress':
        downloaded += event.data.chunkLength;
        const percentage = contentLength
          ? Math.round((downloaded / contentLength) * 100)
          : 0;
        onProgress?.({
          downloaded,
          total: contentLength,
          percentage,
        });
        break;
      case 'Finished':
        onProgress?.({
          downloaded,
          total: downloaded,
          percentage: 100,
        });
        break;
    }
  });
}

/**
 * Restart the application to apply the update
 */
export async function restartApp(): Promise<void> {
  await relaunch();
}

/**
 * Format bytes to human readable string
 */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
