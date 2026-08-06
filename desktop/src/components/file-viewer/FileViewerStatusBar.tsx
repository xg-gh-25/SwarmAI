/**
 * FileViewerStatusBar — Slim bottom bar for the unified FileViewer.
 *
 * Displays file type label, encoding, formatted file size, and any
 * renderer-specific extra info (image dimensions, PDF page count, etc.).
 * Uses CSS-variable theming and stays at a fixed ~28px height.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import type { FileViewType } from './utils/fileViewTypes';
import { getFileTypeInfo } from './utils/fileViewTypes';
import { copyToClipboard } from '../../utils/clipboard';

export interface FileViewerStatusBarProps {
  fileName: string;
  fileSize: number;
  viewType: FileViewType;
  encoding?: string;
  /** Renderer-specific key/value pairs (e.g. "Dimensions": "1920x1080"). */
  extraInfo?: Record<string, string>;
  /** Absolute file path — when present, the bar renders a file-operation cluster
   *  (copy-path + optional attach). Non-text renderers (html/image/pdf/csv) have
   *  no footer of their own, so this brings them to parity with FileEditorCore's
   *  header actions. Absent → the bar is info-only (default, unchanged). */
  filePath?: string;
  /** Attach-to-chat handler — when provided (alongside filePath), an attach button
   *  is shown. Omitted → no attach button (copy-path still shown if filePath set). */
  onAttach?: () => void;
  /** Close handler — when provided, a close button is rendered (Bug 1: non-text
   *  renderers, html/image/pdf/csv, had NO close affordance in panel variant — only
   *  FileEditorCore's text/md/svg footer could close. This brings them to parity).
   *  NOT gated on filePath: a close must be reachable even for a file with no path.
   *  Wired to FileViewer.handleCloseActive → closes the active tab (→ canvas.close
   *  when it's the last tab). Omitted → no close button (default, unchanged). */
  onClose?: () => void;
}

/** Format bytes into a human-readable string (KB / MB / GB). */
function formatFileSize(bytes: number): string {
  if (bytes < 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export default function FileViewerStatusBar({
  fileName,
  fileSize,
  viewType,
  encoding,
  extraInfo,
  filePath,
  onAttach,
  onClose,
}: FileViewerStatusBarProps) {
  const info = getFileTypeInfo(fileName);
  const extraEntries = extraInfo ? Object.entries(extraInfo) : [];

  const [copied, setCopied] = useState(false);
  // Hold the "Copied!" reset timer in a ref so we can cancel it on unmount —
  // otherwise the 2s callback fires after the component is gone and setState on
  // an unmounted component leaks (adversarial F1, conf 9).
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (copyTimerRef.current) clearTimeout(copyTimerRef.current); }, []);
  const handleCopy = useCallback(async () => {
    if (!filePath || copied) return;
    const ok = await copyToClipboard(filePath);
    if (ok) {
      setCopied(true);
      copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }
  }, [filePath, copied]);

  return (
    <div
      className="flex items-center justify-between px-3 border-t border-[var(--color-border)] text-[var(--color-text-secondary)] select-none shrink-0"
      style={{ height: 28, fontSize: 11 }}
      data-view-type={viewType}
    >
      {/* Left: file type + encoding */}
      <div className="flex items-center gap-2 min-w-0">
        <span className="flex items-center gap-1">
          <span
            className="material-symbols-outlined text-[12px] leading-none opacity-60"
            aria-hidden="true"
          >
            {info.icon}
          </span>
          <span>{info.label}</span>
        </span>

        {encoding && (
          <>
            <span className="opacity-40">&middot;</span>
            <span>{encoding}</span>
          </>
        )}
      </div>

      {/* Right: file size + extra info + optional file-operation cluster */}
      <div className="flex items-center gap-2">
        <span>{formatFileSize(fileSize)}</span>

        {extraEntries.map(([key, value]) => (
          <span key={key} className="flex items-center gap-1">
            <span className="opacity-40">&middot;</span>
            <span>
              {key}: {value}
            </span>
          </span>
        ))}

        {/* File-operation cluster — parity with FileEditorCore's header actions
            for renderers (html/image/pdf/csv) that have no footer of their own.
            Shows when the file has a path (copy/attach) OR a close handler is wired
            (close). Close is NOT gated on filePath — a file with no path must still
            be closable (Bug 1). */}
        {(filePath || onClose) && (
          <>
            <span className="opacity-40">&middot;</span>
            {onAttach && filePath && (
              <button
                onClick={onAttach}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
                title="Attach to chat"
                aria-label="Attach to chat"
                data-testid="statusbar-attach"
              >
                <span className="material-symbols-outlined text-[13px] leading-none">attach_file</span>
              </button>
            )}
            {filePath && (
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
                title={copied ? 'Copied!' : 'Copy absolute file path'}
                aria-label="Copy file path"
                data-testid="statusbar-copy-path"
              >
                <span className="material-symbols-outlined text-[13px] leading-none">
                  {copied ? 'check' : 'content_copy'}
                </span>
              </button>
            )}
            {onClose && (
              <button
                onClick={onClose}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
                title="Close this file"
                aria-label="Close file"
                data-testid="statusbar-close"
              >
                <span className="material-symbols-outlined text-[13px] leading-none">close</span>
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
