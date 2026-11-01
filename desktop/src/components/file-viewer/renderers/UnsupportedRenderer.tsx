/**
 * UnsupportedRenderer -- Friendly info card for file types that cannot be
 * previewed in the FileViewer, with Hive-aware action buttons.
 *
 * Features:
 *   - Centered layout with large type-specific Material icon
 *   - File name, type label, file size
 *   - Per-type helpful guidance text (Office, Archive, Database, etc.)
 *   - Action buttons:
 *     - "Open in System App" (hidden on Hive/web via isDesktop() — the canonical
 *        services/tauri SSOT that checks __TAURI_INTERNALS__ (Tauri 2.x) || __TAURI__)
 *     - "Attach to Chat" (when onAttachToChat is provided)
 *     - "Copy Path"
 *   - Uses getFileTypeInfo from fileViewTypes for icon/label resolution
 */
import { useCallback } from 'react';
import { getFileTypeInfo, OFFICE_EXTENSIONS } from '../utils/fileViewTypes';
import { openInSystemApp, revealInFolder } from '../../../utils/openExternal';
import { CopyPathButton, copyPathNow } from '../../common/CopyPathButton';
import { isDesktop } from '../../../services/tauri';
import { useTranslation } from 'react-i18next';
import { fileManagerName } from '../../../utils/platform';

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  /** PHYSICAL absolute path (run_405d221c) — required for OS-level open/reveal/copy.
   *  A workspace-relative filePath is meaningless to the OS opener. Falls back to
   *  filePath only when the meta fetch couldn't resolve it (fail-safe). */
  absolutePath?: string;
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
 * Returns the i18n key of a per-category helpful hint for unsupported file types
 * (render it with t(key, { fileManager })).
 */
function getHelpfulText(ext: string): string {
  // Office set = the shared SSOT (run_a3292e2f) — no local copy to drift.
  const officeExts = OFFICE_EXTENSIONS;
  const archiveExts = new Set(['zip', 'tar', 'gz', 'bz2', 'xz', 'rar', '7z', 'jar', 'war']);
  const diskImageExts = new Set(['dmg', 'iso']);
  const fontExts = new Set(['ttf', 'otf', 'woff', 'woff2']);
  const dbExts = new Set(['sqlite', 'db', 'mdb']);
  const imageExts = new Set(['tiff', 'tif', 'heic', 'heif', 'raw', 'cr2', 'nef']);
  const execExts = new Set(['exe', 'dll', 'so', 'dylib', 'wasm']);
  const compiledExts = new Set(['pyc', 'class', 'o', 'a']);

  // Per-app guidance (A+B) — name the actual native apps so the user knows exactly
  // where this opens. macOS-first (Numbers/Keynote/Pages) since that's the runtime.
  const wordExts = new Set(['docx', 'doc', 'odt']);
  const sheetExts = new Set(['xlsx', 'xls', 'ods']);
  const slideExts = new Set(['pptx', 'ppt', 'odp']);
  if (slideExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpSlides';
  }
  if (sheetExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpSheets';
  }
  if (wordExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpDocs';
  }
  if (officeExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpOffice';
  }
  if (archiveExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpArchive';
  }
  if (diskImageExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpDiskImage';
  }
  if (fontExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpFont';
  }
  if (dbExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpDb';
  }
  if (imageExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpImage';
  }
  if (execExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpExec';
  }
  if (compiledExts.has(ext)) {
    return 'ui.UnsupportedRenderer.helpCompiled';
  }
  if (['mkv', 'avi', 'wmv', 'flv'].includes(ext)) {
    return 'ui.UnsupportedRenderer.helpVideo';
  }
  if (['pages', 'numbers', 'key'].includes(ext)) {
    return 'ui.UnsupportedRenderer.helpIwork';
  }
  if (['parquet', 'feather', 'arrow', 'npy', 'npz', 'pkl', 'pickle', 'pt', 'pth', 'ckpt', 'safetensors', 'onnx', 'h5', 'hdf5'].includes(ext)) {
    return 'ui.UnsupportedRenderer.helpData';
  }
  return 'ui.UnsupportedRenderer.helpDefault';
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function UnsupportedRenderer({
  filePath,
  fileName,
  fileSize,
  absolutePath,
  onAttachToChat,
}: UnsupportedRendererProps) {
  const { t } = useTranslation();
  const ext = getExtension(fileName);
  // A file routed as text that the server found binary (415) has a "text" type
  // info; on this card it is a binary file (canvas-file-types.md P0 B3).
  const routed = getFileTypeInfo(fileName);
  const typeInfo = routed.category === 'text' ? { label: t('ui.UnsupportedRenderer.binaryFile'), icon: 'draft', category: 'binary' as const } : routed;
  const helpText = t(getHelpfulText(ext), { fileManager: fileManagerName() });
  // Canonical SSOT (services/tauri) — checks __TAURI_INTERNALS__ (Tauri 2.x) OR
  // __TAURI__ (1.x). A local `'__TAURI__' in window` check was always false in the
  // packaged 2.x app, hiding these OS actions (run_847ed9f9).
  const showTauriButton = isDesktop();
  // OS-level actions need the PHYSICAL absolute path (run_405d221c). Fall back to
  // filePath only when the meta fetch couldn't resolve it (fail-safe — a relative
  // path may still work if the opener's cwd happens to match, and is better than
  // a dead button).
  const osPath = absolutePath || filePath;
  // Copy-Path is the ONLY action shown on Hive/web (Open + Reveal are Tauri-gated).
  // There, the backend is a REMOTE box, so its absolute path (/home/ubuntu/…) is
  // meaningless on the user's local machine — copy the workspace-relative path
  // instead. In Tauri (local backend) the absolute path is what Finder/terminal want.
  const copyPath = showTauriButton ? osPath : filePath;

  const handleOpenInSystemApp = useCallback(async () => {
    try {
      await openInSystemApp(osPath);
    } catch {
      // Fallback: copy the path instead (toast on failure)
      await copyPathNow(osPath);
    }
  }, [osPath]);

  const handleRevealInFolder = useCallback(async () => {
    try {
      await revealInFolder(osPath);
    } catch {
      await copyPathNow(osPath);
    }
  }, [osPath]);

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
            className="material-symbols-outlined icon-xl"
            style={{ color: 'var(--color-text-muted)' }}
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

        {/* Message — this format is NOT previewable in Swarm (not "yet": Swarm
            renders text/image/pdf/html/av/csv; Office/binary open in their native
            app by design). State it plainly + point to the local-app path. */}
        <p className="text-sm text-[var(--color-text-muted)]">
          {t('ui.UnsupportedRenderer.noPreview')}
        </p>
        <p className="text-xs text-[var(--color-text-dim)] leading-relaxed">
          {helpText}
        </p>

        {/* Action buttons */}
        <div className="flex items-center gap-2 flex-wrap justify-center mt-2">
          {showTauriButton && (
            <button
              onClick={handleOpenInSystemApp}
              className="btn-md btn-primary transition-opacity"
            >
              <span className="material-symbols-outlined icon-sm">open_in_new</span>
              {t('ui.UnsupportedRenderer.openDefaultApp')}
            </button>
          )}

          {showTauriButton && (
            <button
              onClick={handleRevealInFolder}
              className="btn-md btn-secondary transition-colors"
              title={t('ui.UnsupportedRenderer.revealTitle', { fileManager: fileManagerName() })}
            >
              <span className="material-symbols-outlined icon-sm">folder_open</span>
              {t('ui.UnsupportedRenderer.reveal', { fileManager: fileManagerName() })}
            </button>
          )}

          {onAttachToChat && (
            <button
              onClick={handleAttachToChat}
              className="btn-md btn-secondary transition-colors"
            >
              <span className="material-symbols-outlined icon-sm">attach_file</span>
              {t('ui.UnsupportedRenderer.attachToChat')}
            </button>
          )}

          <CopyPathButton
            path={copyPath}
            label={t('common.copy.path')}
            className="btn-md btn-ghost"
          />
        </div>
      </div>
    </div>
  );
}
