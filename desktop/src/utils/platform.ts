/**
 * The OS the app runs on, and the labels that depend on it.
 *
 * Read from the webview's navigator (no Tauri call, so it works in tests and in a
 * browser too). Use these instead of writing "⌘" or "Finder" into UI text:
 * - modKeyLabel(): the shortcut modifier, '⌘' on macOS, 'Ctrl' elsewhere. Locale
 *   strings can say {{mod}} without passing it (i18n/index.ts sets it as a default
 *   variable).
 * - fileManagerName(): Finder / File Explorer / Files, translated.
 * - defaultDataDir() / joinDir(): the data dir as shown to the user. Show the real
 *   one from /api/system/status (`dataDir`) when there is one; defaultDataDir() is
 *   only for before the backend has answered.
 *
 * @exports detectOs, isMac, isWindows, modKeyLabel, fileManagerName, defaultDataDir,
 *   joinDir, OsKind
 */
import i18next from 'i18next';

export type OsKind = 'mac' | 'windows' | 'linux';

/** The OS from userAgentData.platform, else navigator.platform, else the user agent. */
export function detectOs(): OsKind {
  if (typeof navigator === 'undefined') return 'linux';
  const uaData = (navigator as unknown as { userAgentData?: { platform?: string } }).userAgentData;
  const p = [uaData?.platform, navigator.platform, navigator.userAgent].filter(Boolean).join(' ');
  if (/mac|iphone|ipad|ipod/i.test(p)) return 'mac';
  // "windows" / "Win32" / "Win64" only: a bare /win/ also matches "darwin".
  if (/windows|win32|win64/i.test(p)) return 'windows';
  return 'linux';
}

export function isMac(): boolean {
  return detectOs() === 'mac';
}

export function isWindows(): boolean {
  return detectOs() === 'windows';
}

/** The shortcut modifier key as shown to the user: '⌘' on macOS, 'Ctrl' elsewhere. */
export function modKeyLabel(): string {
  return isMac() ? '⌘' : 'Ctrl';
}

/** The OS file manager's name in the current UI language. */
export function fileManagerName(): string {
  return i18next.t(`platform.fileManager.${detectOs()}`);
}

/** The default app data dir in this OS's notation (the backend's own default). */
export function defaultDataDir(): string {
  return isWindows() ? '%USERPROFILE%\\.swarm-ai\\' : '~/.swarm-ai/';
}

/** *dir* with sub folder *name*, using the separator *dir* already uses; ends with one. */
export function joinDir(dir: string, name: string): string {
  const sep = dir.includes('\\') && !dir.includes('/') ? '\\' : '/';
  const base = dir.replace(/[\\/]+$/, '');
  return name ? `${base}${sep}${name}${sep}` : `${base}${sep}`;
}
