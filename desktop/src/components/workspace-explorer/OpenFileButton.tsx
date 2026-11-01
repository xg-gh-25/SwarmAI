/**
 * OpenFileButton — Opens a native file picker dialog to select any file on disk.
 *
 * Uses @tauri-apps/plugin-dialog (already registered in Rust capabilities).
 * Falls back gracefully in non-Tauri environments (dev mode with browser).
 * Dispatches 'swarm:open-file' event with selected path.
 */

import { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';

export function OpenFileButton() {
  const { t } = useTranslation();
  const handleClickRef = useRef<(() => void) | undefined>(undefined);

  const handleClick = useCallback(async () => {
    try {
      // Dynamic import — only loads in Tauri runtime
      const { open } = await import('@tauri-apps/plugin-dialog');
      const selected = await open({
        multiple: false,
        title: t('explorer.openFileDialogTitle'),
      });
      if (selected && typeof selected === 'string') {
        document.dispatchEvent(
          new CustomEvent(OPEN_FILE_EVENT, { detail: { path: selected } }),
        );
      }
    } catch (err) {
      // Non-Tauri environment (dev mode) — fall back to browser file input
      console.warn('[OpenFileButton] Tauri dialog unavailable:', err);
      const input = document.createElement('input');
      input.type = 'file';
      input.onchange = () => {
        const file = input.files?.[0];
        if (file) {
          // Browser File API doesn't give absolute paths — dispatch name as best effort
          document.dispatchEvent(
            new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.name } }),
          );
        }
      };
      input.click();
    }
  }, [t]);

  handleClickRef.current = handleClick;

  // Listen for the Cmd/Ctrl+O keyboard shortcut event from ThreeColumnLayout
  useEffect(() => {
    const handler = () => handleClickRef.current?.();
    document.addEventListener('swarm:open-file-dialog', handler);
    return () => document.removeEventListener('swarm:open-file-dialog', handler);
  }, []);

  return (
    <button
      onClick={handleClick}
      className="icon-btn text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
      title={t('explorer.openFileShortcut')}
      aria-label={t('explorer.openFile')}
      data-testid="open-file-button"
    >
      <span className="material-symbols-outlined icon-sm">folder_open</span>
    </button>
  );
}
