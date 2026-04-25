/**
 * Wrappers around @tauri-apps/plugin-opener for opening URLs and files.
 *
 * Tauri 2.x webview silently ignores <a target="_blank"> — clicks do nothing.
 * These utilities use plugin-opener (registered in lib.rs) with fallbacks
 * for browser dev mode (npm run dev without Tauri).
 *
 * @exports openExternal — open a URL in system browser
 * @exports openInSystemApp — open a file path in its default system app
 */

/** Open a URL in the system's default browser. */
export async function openExternal(url: string): Promise<void> {
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl(url);
  } catch {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

/** Open a local file path in its default system application (e.g. Preview for PDF). */
export async function openInSystemApp(filePath: string): Promise<void> {
  const { openPath } = await import('@tauri-apps/plugin-opener');
  await openPath(filePath);
}
