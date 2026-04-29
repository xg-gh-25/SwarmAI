/**
 * UnsupportedRenderer -- Friendly info card for file types that cannot be
 * previewed in the FileViewer, with Hive-aware action buttons.
 *
 * Features:
 *   - Centered layout with large type-specific Material icon
 *   - File name, type label, file size
 *   - Per-type helpful guidance text (Office, Archive, Database, etc.)
 *   - Action buttons:
 *     - "Open in System App" (hidden when window.__TAURI__ is undefined -- Hive/web)
 *     - "Attach to Chat" (when onAttachToChat is provided)
 *     - "Copy Path"
 *   - Uses getFileTypeInfo from fileViewTypes for icon/label resolution
 */
import { useState, useCallback } from 'react';
import { getFileTypeInfo } from '../utils/fileViewTypes';
import { openInSystemApp } from '../../../utils/openExternal';
import { copyToClipboard } from '../../../utils/clipboard';

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

interface UnsupportedRendererProps extends RendererProps {
  onAttachToChat?: (filePath: string) => void;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function getExtension(fileName: string): string {
  const parts = fileName.split('.');
  return parts.length > 1 ? parts.pop()!.toLowerCase() : '';
}

/**
 * Returns a per-category helpful hint for unsupported file types.
 */
function getHelpfulText(ext: string): string {
  const officeExts = new Set(['docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'odt', 'ods', 'odp']);
  const archiveExts = new Set(['zip', 'tar', 'gz', 'bz2', 'xz', 'rar', '7z', 'jar', 'war']);
  const diskImageExts = new Set(['dmg', 'iso']);
  const fontExts = new Set(['ttf', 'otf', 'woff', 'woff2']);
  const dbExts = new Set(['sqlite', 'db', 'mdb']);
  const imageExts = new Set(['tiff', 'tif', 'heic', 'heif', 'raw', 'cr2', 'nef']);
  const execExts = new Set(['exe', 'dll', 'so', 'dylib', 'wasm']);
  const compiledExts = new Set(['pyc', 'class', 'o', 'a']);

  if (officeExts.has(ext)) {
    return 'Office documents can be analyzed by attaching to a chat session, or opened in their native application.';
  }
  if (archiveExts.has(ext)) {
    return 'Archives can be extracted via terminal commands (unzip, tar, etc.) or opened in your system file manager.';
  }
  if (diskImageExts.has(ext)) {
    return 'Disk images can be mounted by double-clicking in your system file manager.';
  }
  if (fontExts.has(ext)) {
    return 'Font files can be previewed in Font Book or your system font manager.';
  }
  if (dbExts.has(ext)) {
    return 'Database files can be opened with DB Browser, sqlite3 CLI, or attached to chat for query analysis.';
  }
  if (imageExts.has(ext)) {
    return 'This image format requires a native viewer. Open it in Preview or your preferred image editor.';
  }
  if (execExts.has(ext)) {
    return 'Compiled binaries cannot be previewed. Use a disassembler or hex editor for inspection.';
  }
  if (compiledExts.has(ext)) {
    return 'Compiled bytecode cannot be displayed as text. Decompile it or inspect the source file instead.';
  }
  return 'This file can be opened in its default system application.';
}

/** Check if we are running inside a Tauri desktop shell. */
function isTauriContext(): boolean {
  return typeof window !== 'undefined' && '__TAURI__' in window;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function UnsupportedRenderer({
  filePath,
  fileName,
  fileSize,
  onAttachToChat,
}: UnsupportedRendererProps) {
  const [copyFeedback, setCopyFeedback] = useState(false);

  const ext = getExtension(fileName);
  const typeInfo = getFileTypeInfo(fileName);
  const helpText = getHelpfulText(ext);
  const showTauriButton = isTauriContext();

  const handleOpenInSystemApp = useCallback(async () => {
    try {
      await openInSystemApp(filePath);
    } catch {
      // Fallback: copy path instead
      await copyToClipboard(filePath);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    }
  }, [filePath]);

  const handleCopyPath = useCallback(async () => {
    if (copyFeedback) return;
    const ok = await copyToClipboard(filePath);
    if (ok) {
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    }
  }, [filePath, copyFeedback]);

  const handleAttachToChat = useCallback(() => {
    onAttachToChat?.(filePath);
  }, [filePath, onAttachToChat]);

  return (
    <div className="flex items-center justify-center h-full w-full p-8">
      <div className="flex flex-col items-center text-center max-w-md gap-4">
        {/* Icon */}
        <div
          className="flex items-center justify-center w-20 h-20 rounded-2xl"
          style={{ backgroundColor: 'var(--color-hover)' }}
        >
          <span
            className="material-symbols-outlined"
            style={{ fontSize: '40px', color: 'var(--color-text-muted)' }}
          >
            {typeInfo.icon}
          </span>
        </div>

        {/* File info */}
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-[var(--color-text)] break-all">
            {fileName}
          </p>
          <div className="flex items-center justify-center gap-2 flex-wrap">
            <span
              className="inline-block px-2 py-0.5 text-xs font-medium rounded"
              style={{
                backgroundColor: 'var(--color-hover)',
                color: 'var(--color-text-muted)',
              }}
            >
              {typeInfo.label}
            </span>
            {fileSize > 0 && (
              <span className="text-xs text-[var(--color-text-muted)]">
                {formatFileSize(fileSize)}
              </span>
            )}
          </div>
        </div>

        {/* Message */}
        <p className="text-sm text-[var(--color-text-muted)]">
          This file type can't be previewed in Swarm yet.
        </p>
        <p className="text-xs text-[var(--color-text-dim)] leading-relaxed">
          {helpText}
        </p>

        {/* Action buttons */}
        <div className="flex items-center gap-2 flex-wrap justify-center mt-2">
          {showTauriButton && (
            <button
              onClick={handleOpenInSystemApp}
              className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg
                bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
            >
              <span className="material-symbols-outlined text-base">open_in_new</span>
              Open in System App
            </button>
          )}

          {onAttachToChat && (
            <button
              onClick={handleAttachToChat}
              className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg
                text-[var(--color-text)] hover:bg-[var(--color-hover)]
                border border-[var(--color-border)] transition-colors"
            >
              <span className="material-symbols-outlined text-base">attach_file</span>
              Attach to Chat
            </button>
          )}

          <button
            onClick={handleCopyPath}
            className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg
              text-[var(--color-text-muted)] hover:text-[var(--color-text)]
              hover:bg-[var(--color-hover)] border border-[var(--color-border)] transition-colors"
            title="Copy absolute file path"
          >
            <span className="material-symbols-outlined text-base">
              {copyFeedback ? 'check' : 'content_copy'}
            </span>
            {copyFeedback ? 'Copied!' : 'Copy Path'}
          </button>
        </div>
      </div>
    </div>
  );
}
