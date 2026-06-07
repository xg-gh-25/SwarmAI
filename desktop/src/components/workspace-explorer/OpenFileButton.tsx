/**
 * OpenFileButton — Opens a native file picker dialog to select any file on disk.
 *
 * Uses @tauri-apps/plugin-dialog (already registered in Rust capabilities).
 * Falls back gracefully in non-Tauri environments (dev mode with browser).
 * Dispatches 'swarm:open-file' event with selected path.
 */

import { useCallback, useEffect, useRef } from 'react';
import { OPEN_FILE_EVENT } from '../common/MarkdownRenderer';

export function OpenFileButton() {
  const handleClickRef = useRef<() => void>();

  const handleClick = useCallback(async () => {
    try {
      // Dynamic import — only loads in Tauri runtime
      const { open } = await import('@tauri-apps/plugin-dialog');
      const selected = await open({
        multiple: false,
        title: 'Open File',
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
  }, []);

  handleClickRef.current = handleClick;

  // Listen for Cmd+O keyboard shortcut event from ThreeColumnLayout
  useEffect(() => {
    const handler = () => handleClickRef.current?.();
    document.addEventListener('swarm:open-file-dialog', handler);
    return () => document.removeEventListener('swarm:open-file-dialog', handler);
  }, []);

  return (
    <button
      onClick={handleClick}
      className="p-1 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
      title="Open file from disk (⌘O)"
      aria-label="Open file from disk"
      data-testid="open-file-button"
    >
      <span className="material-symbols-outlined text-sm">folder_open</span>
    </button>
  );
}
