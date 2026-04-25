/**
 * Open a URL in the system's default browser.
 *
 * Tauri 2.x webview silently ignores <a target="_blank"> — clicks do nothing.
 * This utility uses @tauri-apps/plugin-opener (already registered in lib.rs)
 * with a fallback to window.open for browser dev mode.
 *
 * @exports openExternal
 */
export async function openExternal(url: string): Promise<void> {
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl(url);
  } catch {
    // Fallback for browser dev mode (npm run dev without Tauri)
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}
