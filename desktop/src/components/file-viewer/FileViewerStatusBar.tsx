/**
 * FileViewerStatusBar — Slim bottom bar for the unified FileViewer.
 *
 * Displays file type label, encoding, formatted file size, and any
 * renderer-specific extra info (image dimensions, PDF page count, etc.).
 * Uses CSS-variable theming and stays at a fixed ~28px height.
 */

import type { FileViewType } from './utils/fileViewTypes';
import { getFileTypeInfo } from './utils/fileViewTypes';

export interface FileViewerStatusBarProps {
  fileName: string;
  fileSize: number;
  viewType: FileViewType;
  encoding?: string;
  /** Renderer-specific key/value pairs (e.g. "Dimensions": "1920x1080"). */
  extraInfo?: Record<string, string>;
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
}: FileViewerStatusBarProps) {
  const info = getFileTypeInfo(fileName);
  const extraEntries = extraInfo ? Object.entries(extraInfo) : [];

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

      {/* Right: file size + extra info */}
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
      </div>
    </div>
  );
}
