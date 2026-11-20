/**
 * Wrappers around @tauri-apps/plugin-opener for opening URLs and files.
 *
 * Tauri 2.x webview silently ignores <a target="_blank"> — clicks do nothing.
 * These utilities use plugin-opener (registered in lib.rs) with fallbacks
 * for browser dev mode (npm run dev without Tauri).
 *
 * @exports openExternal — open a URL in system browser
 * @exports openInSystemApp — open a file path in its default system app
 * @exports revealInFolder — reveal a file in the OS file manager (Finder/Explorer)
 */

/** The schemes a link may open with. Anything else (file:, javascript:, a custom app
 *  scheme) is refused here instead of being handed to the webview: the opener plugin's own
 *  scope refuses those too, and the old catch-all fallback then passed them to
 *  window.open, which bypassed that scope. Local files go through openInSystemApp. */
const ALLOWED_SCHEMES = new Set(['http:', 'https:', 'mailto:']);

/** Open a URL in the system's default browser. Rejects a URL whose scheme is not
 *  http, https or mailto, before anything is opened. */
export async function openExternal(url: string): Promise<void> {
  let scheme: string;
  try {
    scheme = new URL(url).protocol;
  } catch {
    throw new Error(`not a URL: ${url}`);
  }
  if (!ALLOWED_SCHEMES.has(scheme)) throw new Error(`refused to open a ${scheme} link`);
  try {
    const { openUrl } = await import('@tauri-apps/plugin-opener');
    await openUrl(url);
  } catch {
    // outside Tauri (the browser dev server) the plugin is missing: open a new tab
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

/** Open a local file path in its default system application (e.g. Preview for PDF). */
export async function openInSystemApp(filePath: string): Promise<void> {
  const { openPath } = await import('@tauri-apps/plugin-opener');
  await openPath(filePath);
}

/** Reveal a file in the OS file manager (Finder on macOS, Explorer on Windows,
 *  the default file manager on Linux) with the file selected — distinct from
 *  openInSystemApp, which OPENS the file in its default app. Uses plugin-opener's
 *  revealItemInDir (granted via opener:default's allow-reveal-item-in-dir). */
export async function revealInFolder(filePath: string): Promise<void> {
  const { revealItemInDir } = await import('@tauri-apps/plugin-opener');
  await revealItemInDir(filePath);
}
