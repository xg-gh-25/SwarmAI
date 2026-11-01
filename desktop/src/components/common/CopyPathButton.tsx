/**
 * CopyPathButton — the ONE copy-path button of the file viewer
 * (docs/design/canvas-file-types.md §2b).
 *
 * - Copies an ABSOLUTE path on the desktop: a relative path is first turned into the
 *   physical path with GET /workspace/file/meta (`absolute_path`); nothing is copied
 *   until it is absolute. On Hive / web the backend is a remote box, so the path is
 *   copied as given (its absolute path is meaningless on the user's machine).
 * - Success: the icon turns into a check for 1.5 s and "Copied" is announced
 *   (aria-live). Failure (all clipboard strategies failed, or the path could not be
 *   resolved): a warning toast that shows the path, so it can still be read.
 * - The click never reaches the row / card it sits in (stopPropagation).
 *
 * @exports CopyPathButton, copyPathNow, COPIED_MS
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import api from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';
import { isDesktop } from '../../services/tauri';
// the app's i18next instance (initialised in src/i18n, imported by main.tsx)
import i18n from 'i18next';
import { useTranslation } from 'react-i18next';

export const COPIED_MS = 1500;

const isAbsolute = (p: string) => p.startsWith('/') || /^[A-Za-z]:[\\/]/.test(p);

async function absolutePathOf(path: string): Promise<string | null> {
  if (!isDesktop() || isAbsolute(path)) return path;
  try {
    const { data } = await api.get<{ absolute_path?: string }>('/workspace/file/meta', { params: { path } });
    return data.absolute_path && isAbsolute(data.absolute_path) ? data.absolute_path : null;
  } catch {
    return null;
  }
}

/** Copy a path now (no button): the same resolve + clipboard + failure toast. For
 *  fallbacks such as "open in system app failed → copy the path instead". */
export async function copyPathNow(path: string): Promise<boolean> {
  const target = await absolutePathOf(path);
  if (!target) { toast(i18n.t('ui.CopyPathButton.resolveFailed', { path })); return false; }
  if (await copyToClipboard(target)) return true;
  toast(i18n.t('ui.CopyPathButton.copyFailed', { path: target }));
  return false;
}

function toast(message: string) {
  document.dispatchEvent(new CustomEvent('swarm:toast', {
    detail: { message, severity: 'warning', id: 'copy-path-failed' },
  }));
}

export function CopyPathButton({
  path, label, className = 'icon-btn', iconSize = 'sm', testId = 'copy-path-btn',
}: {
  /** The file's path in any form the viewer has (absolute, workspace- or repo-relative). */
  path: string;
  /** Show a text label next to the icon (modal header, info card); icon only when absent. */
  label?: string;
  /** Size / colour classes of the button (callers place it in their own bars). */
  className?: string;
  /** The glyph's tier on the one icon scale (index.css): sm in an icon-btn, xs in a dense bar. */
  iconSize?: 'xs' | 'sm';
  testId?: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const onClick = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (await copyPathNow(path)) {
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), COPIED_MS);
    }
  }, [path]);

  return (
    <button
      type="button"
      onClick={onClick}
      className={`${className} transition-colors`}
      // inline, so it wins over any text-* colour class the caller passes
      style={copied ? { color: 'var(--color-success)' } : undefined}
      title={copied ? t('ui.CopyPathButton.copied') : t('ui.CopyPathButton.copyFullPath')}
      aria-label={copied ? t('ui.CopyPathButton.copied') : t('ui.CopyPathButton.copyFullPath')}
      data-testid={testId}
      data-copied={copied || undefined}
    >
      <span className={iconSize === 'xs' ? 'material-symbols-outlined icon-xs leading-none' : 'material-symbols-outlined icon-sm leading-none'} aria-hidden="true">{copied ? 'check' : 'content_copy'}</span>
      {label && <span>{copied ? t('ui.CopyPathButton.copied') : label}</span>}
      <span className="sr-only" aria-live="polite">{copied ? t('ui.CopyPathButton.copied') : ''}</span>
    </button>
  );
}
