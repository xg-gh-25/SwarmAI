/**
 * Binary file preview modal for images and non-text file types.
 *
 * Opened by ThreeColumnLayout when a user double-clicks a non-text file
 * in the Workspace Explorer.
 *
 * - **Image mode** — `<img>` with data-URI (PNG, JPG, SVG, etc.), zoom + pan
 * - **Unsupported** — file type icon, label, "Open in Default App" + "Copy Path"
 *   (PDF, Office docs, TIFF/HEIC, media, archives, executables)
 *
 * Unsupported mode skips content fetch — only shows metadata and action buttons.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import clsx from 'clsx';
import api from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';

export interface BinaryPreviewModalProps {
  isOpen: boolean;
  fileName: string;
  filePath: string;
  mode: 'image' | 'unsupported';
  onClose: () => void;
}

interface FileResponse {
  content: string;
  encoding: string;
  mime_type?: string;
  mimeType?: string;
  size?: number;
  name: string;
  path: string;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getExtension(fileName: string): string {
  const parts = fileName.split('.');
  return parts.length > 1 ? parts.pop()!.toUpperCase() : '';
}

/* ------------------------------------------------------------------ */
/*  Unsupported file type metadata for friendly UX                     */
/* ------------------------------------------------------------------ */

interface FileTypeInfo {
  icon: string;       // Material Symbols icon name
  color: string;      // Accent color for the icon & badge
  label: string;      // Human-readable type label (e.g. "Word Document")
  message: string;    // Friendly explanation shown in the modal
  action: string;     // CTA button label
}

const FILE_TYPE_MAP: Record<string, FileTypeInfo> = {
  // PDF
  pdf:  { icon: 'picture_as_pdf', color: '#EA4335', label: 'PDF Document',        message: 'PDF documents can be opened in Preview or Adobe Acrobat.',             action: 'Open in Default App' },
  // Documents
  doc:  { icon: 'description',   color: '#4285F4', label: 'Word Document',       message: 'Word documents can be opened in Microsoft Word or Pages.',             action: 'Open in Default App' },
  docx: { icon: 'description',   color: '#4285F4', label: 'Word Document',       message: 'Word documents can be opened in Microsoft Word or Pages.',             action: 'Open in Default App' },
  xls:  { icon: 'table_chart',   color: '#0F9D58', label: 'Excel Spreadsheet',   message: 'Spreadsheets can be opened in Microsoft Excel or Numbers.',            action: 'Open in Default App' },
  xlsx: { icon: 'table_chart',   color: '#0F9D58', label: 'Excel Spreadsheet',   message: 'Spreadsheets can be opened in Microsoft Excel or Numbers.',            action: 'Open in Default App' },
  ppt:  { icon: 'slideshow',     color: '#DB4437', label: 'PowerPoint',          message: 'Presentations can be opened in Microsoft PowerPoint or Keynote.',      action: 'Open in Default App' },
  pptx: { icon: 'slideshow',     color: '#DB4437', label: 'PowerPoint',          message: 'Presentations can be opened in Microsoft PowerPoint or Keynote.',      action: 'Open in Default App' },
  // Images (non-browser-renderable)
  tiff: { icon: 'image',         color: '#FF9800', label: 'TIFF Image',          message: 'TIFF images can be opened in Preview or any image viewer.',            action: 'Open in Default App' },
  tif:  { icon: 'image',         color: '#FF9800', label: 'TIFF Image',          message: 'TIFF images can be opened in Preview or any image viewer.',            action: 'Open in Default App' },
  heic: { icon: 'image',         color: '#FF9800', label: 'HEIC Image',          message: 'HEIC images can be opened in Preview or Photos.',                     action: 'Open in Default App' },
  heif: { icon: 'image',         color: '#FF9800', label: 'HEIF Image',          message: 'HEIF images can be opened in Preview or Photos.',                     action: 'Open in Default App' },
  // Media — Audio
  mp3:  { icon: 'music_note',    color: '#E91E63', label: 'Audio File',          message: 'Audio files can be played in your default music player.',              action: 'Open in Music Player' },
  wav:  { icon: 'music_note',    color: '#E91E63', label: 'Audio File',          message: 'Audio files can be played in your default music player.',              action: 'Open in Music Player' },
  flac: { icon: 'music_note',    color: '#E91E63', label: 'Audio File',          message: 'Audio files can be played in your default music player.',              action: 'Open in Music Player' },
  ogg:  { icon: 'music_note',    color: '#E91E63', label: 'Audio File',          message: 'Audio files can be played in your default music player.',              action: 'Open in Music Player' },
  // Media — Video
  mp4:  { icon: 'movie',         color: '#9C27B0', label: 'Video File',          message: 'Video files can be played in your default video player.',              action: 'Open in Video Player' },
  avi:  { icon: 'movie',         color: '#9C27B0', label: 'Video File',          message: 'Video files can be played in your default video player.',              action: 'Open in Video Player' },
  mov:  { icon: 'movie',         color: '#9C27B0', label: 'Video File',          message: 'Video files can be played in your default video player.',              action: 'Open in Video Player' },
  mkv:  { icon: 'movie',         color: '#9C27B0', label: 'Video File',          message: 'Video files can be played in your default video player.',              action: 'Open in Video Player' },
  // Archives
  zip:  { icon: 'folder_zip',    color: '#795548', label: 'Archive',             message: 'Archives can be extracted with your system\'s built-in tools.',        action: 'Reveal in Finder' },
  tar:  { icon: 'folder_zip',    color: '#795548', label: 'Archive',             message: 'Archives can be extracted with your system\'s built-in tools.',        action: 'Reveal in Finder' },
  gz:   { icon: 'folder_zip',    color: '#795548', label: 'Archive',             message: 'Archives can be extracted with your system\'s built-in tools.',        action: 'Reveal in Finder' },
  rar:  { icon: 'folder_zip',    color: '#795548', label: 'Archive',             message: 'Archives can be extracted with your system\'s built-in tools.',        action: 'Reveal in Finder' },
  '7z': { icon: 'folder_zip',    color: '#795548', label: 'Archive',             message: 'Archives can be extracted with your system\'s built-in tools.',        action: 'Reveal in Finder' },
  // Disk images & executables
  dmg:  { icon: 'save',          color: '#607D8B', label: 'Disk Image',          message: 'Disk images can be mounted by double-clicking in Finder.',            action: 'Reveal in Finder' },
  iso:  { icon: 'save',          color: '#607D8B', label: 'Disk Image',          message: 'Disk images can be mounted by double-clicking in Finder.',            action: 'Reveal in Finder' },
  exe:  { icon: 'settings',      color: '#607D8B', label: 'Executable',          message: 'This file type cannot be previewed here.',                            action: 'Reveal in Finder' },
  dll:  { icon: 'settings',      color: '#607D8B', label: 'Library',             message: 'Binary libraries cannot be previewed.',                               action: 'Reveal in Finder' },
  so:   { icon: 'settings',      color: '#607D8B', label: 'Shared Library',      message: 'Binary libraries cannot be previewed.',                               action: 'Reveal in Finder' },
  dylib:{ icon: 'settings',      color: '#607D8B', label: 'Dynamic Library',     message: 'Binary libraries cannot be previewed.',                               action: 'Reveal in Finder' },
  wasm: { icon: 'memory',        color: '#607D8B', label: 'WebAssembly',         message: 'WebAssembly binaries cannot be previewed.',                           action: 'Reveal in Finder' },
};

const FALLBACK_FILE_TYPE: FileTypeInfo = {
  icon: 'draft',
  color: '#9E9E9E',
  label: 'Binary File',
  message: 'This file type cannot be previewed in SwarmAI.',
  action: 'Open in Default App',
};

function getFileTypeInfo(ext: string): FileTypeInfo {
  return FILE_TYPE_MAP[ext] ?? FALLBACK_FILE_TYPE;
}

export default function BinaryPreviewModal({
  isOpen,
  fileName,
  filePath,
  mode,
  onClose,
}: BinaryPreviewModalProps) {
  const [content, setContent] = useState<string | null>(null);
  const [mimeType, setMimeType] = useState<string>('application/octet-stream');
  const [fileSize, setFileSize] = useState<number>(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyFeedback, setCopyFeedback] = useState(false);

  // Image viewer state
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [imageDimensions, setImageDimensions] = useState({ w: 0, h: 0 });

  const overlayRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  // Cache workspace root to avoid repeated /workspace API calls
  const wsRootRef = useRef<string | null>(null);

  // Fetch file content (image) or metadata (unsupported) when modal opens
  const fetchContent = useCallback(async () => {
    setLoading(true);
    setError(null);
    setContent(null);
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setImageDimensions({ w: 0, h: 0 });

    try {
      if (mode === 'image') {
        // Image mode: fetch full content for inline rendering
        const response = await api.get<FileResponse>('/workspace/file', {
          params: { path: filePath },
        });
        const data = response.data;
        setContent(data.content);
        const mime = data.mime_type ?? data.mimeType ?? 'application/octet-stream';
        setMimeType(mime);
        setFileSize(data.size ?? 0);
      } else {
        // Unsupported mode: lightweight metadata fetch only (no content)
        const response = await api.get<{ size: number; mime_type: string }>(
          '/workspace/file/meta',
          { params: { path: filePath } },
        );
        setFileSize(response.data.size ?? 0);
        setMimeType(response.data.mime_type ?? 'application/octet-stream');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load file';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [filePath, mode]);

  useEffect(() => {
    if (isOpen) {
      wsRootRef.current = null; // reset cache when a new file opens
      fetchContent();
    }
  }, [isOpen, fetchContent]);

  // Escape key and focus trap
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Focus trap: cycle through focusable elements
      if (e.key === 'Tab' && modalRef.current) {
        const focusable = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey) {
          if (document.activeElement === first) {
            e.preventDefault();
            last.focus();
          }
        } else {
          if (document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  // Image zoom via mouse wheel
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setScale((prev) => {
      const next = prev + (e.deltaY < 0 ? 0.1 : -0.1);
      return Math.max(0.1, Math.min(10, next));
    });
  }, []);

  // Image pan via mouse drag (only when zoomed)
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (scale <= 1) return;
      e.preventDefault();
      setIsDragging(true);
      setDragStart({ x: e.clientX - translate.x, y: e.clientY - translate.y });
    },
    [scale, translate],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDragging) return;
      setTranslate({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    },
    [isDragging, dragStart],
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({ w: img.naturalWidth, h: img.naturalHeight });
  }, []);

  const getAbsolutePath = useCallback(async (): Promise<string> => {
    // If filePath is already absolute, return it as-is
    if (filePath.startsWith('/')) return filePath;
    // Use cached workspace root to avoid repeated API calls
    if (!wsRootRef.current) {
      const configResp = await api.get<{ file_path?: string; filePath?: string }>('/workspace');
      wsRootRef.current = configResp.data.file_path ?? configResp.data.filePath ?? '';
    }
    return wsRootRef.current ? `${wsRootRef.current}/${filePath}` : filePath;
  }, [filePath]);

  const handleOpenInDefaultApp = useCallback(async () => {
    try {
      const absolutePath = await getAbsolutePath();
      const { openPath } = await import('@tauri-apps/plugin-opener');
      await openPath(absolutePath);
    } catch {
      // Fallback: copy path to clipboard
      try {
        const absolutePath = await getAbsolutePath();
        await copyToClipboard(absolutePath);
        setCopyFeedback(true);
        setTimeout(() => setCopyFeedback(false), 2000);
      } catch { /* best effort */ }
    }
  }, [getAbsolutePath]);

  const handleCopyPath = useCallback(async () => {
    if (copyFeedback) return;
    try {
      const absolutePath = await getAbsolutePath();
      await copyToClipboard(absolutePath);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    } catch { /* best effort */ }
  }, [getAbsolutePath, copyFeedback]);

  if (!isOpen) return null;

  // --- Render helpers ---

  const renderLoading = () => (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <div className="w-8 h-8 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
      <span className="text-sm text-[var(--color-text-muted)]">Loading file…</span>
    </div>
  );

  const renderError = () => (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <span className="material-symbols-outlined text-3xl text-red-400">error</span>
      <p className="text-sm text-red-400">{error}</p>
      <button
        onClick={fetchContent}
        className="px-3 py-1.5 text-sm rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
      >
        Retry
      </button>
    </div>
  );

  const renderImageMode = () => {
    if (!content) return null;
    const dataUri = `data:${mimeType};base64,${content}`;
    return (
      <div className="flex flex-col items-center gap-2 overflow-hidden flex-1">
        <div
          className="flex-1 overflow-hidden flex items-center justify-center w-full"
          style={{ cursor: scale > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default' }}
          onWheel={handleWheel}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <img
            src={dataUri}
            alt={fileName}
            onLoad={handleImageLoad}
            style={{
              objectFit: 'contain',
              maxWidth: '100%',
              maxHeight: '100%',
              transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
              transformOrigin: 'center center',
              transition: isDragging ? 'none' : 'transform 0.1s ease-out',
              userSelect: 'none',
            }}
            draggable={false}
          />
        </div>
        <div className="text-xs text-[var(--color-text-muted)] flex items-center gap-3 pb-1">
          {imageDimensions.w > 0 && (
            <span>{imageDimensions.w} × {imageDimensions.h}</span>
          )}
          {fileSize > 0 && <span>{formatFileSize(fileSize)}</span>}
          <span>{Math.round(scale * 100)}%</span>
        </div>
      </div>
    );
  };

  const renderUnsupportedMode = () => {
    const ext = getExtension(fileName).toLowerCase();
    const fileTypeInfo = getFileTypeInfo(ext);
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <span
          className="material-symbols-outlined text-5xl"
          style={{ color: fileTypeInfo.color }}
        >
          {fileTypeInfo.icon}
        </span>
        <div className="text-center">
          <p className="text-sm font-medium text-[var(--color-text)] mb-1">{fileName}</p>
          <div className="flex items-center justify-center gap-2 mb-3">
            {ext && (
              <span
                className="inline-block px-2 py-0.5 text-xs font-medium rounded"
                style={{ backgroundColor: `${fileTypeInfo.color}20`, color: fileTypeInfo.color }}
              >
                {fileTypeInfo.label}
              </span>
            )}
            {fileSize > 0 && (
              <span className="text-xs text-[var(--color-text-muted)]">
                {formatFileSize(fileSize)}
              </span>
            )}
          </div>
          <p
            className="text-sm text-[var(--color-text-muted)]"
            role="status"
            aria-live="polite"
          >
            {fileTypeInfo.message}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleOpenInDefaultApp}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
          >
            <span className="material-symbols-outlined text-base">open_in_new</span>
            {fileTypeInfo.action}
          </button>
          <button
            onClick={handleCopyPath}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] border border-[var(--color-border)] transition-colors"
            title="Copy absolute file path"
          >
            <span className="material-symbols-outlined text-base">
              {copyFeedback ? 'check' : 'content_copy'}
            </span>
            {copyFeedback ? 'Copied!' : 'Copy Path'}
          </button>
        </div>
      </div>
    );
  };

  const renderContent = () => {
    if (loading) return renderLoading();
    if (error) return renderError();
    switch (mode) {
      case 'image':
        return renderImageMode();
      case 'unsupported':
        return renderUnsupportedMode();
      default:
        return null;
    }
  };

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === overlayRef.current) {
          onClose();
        }
      }}
      data-testid="binary-preview-modal"
    >
      <div
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-label={fileName}
        className={clsx(
          'w-full bg-[var(--color-card)] border border-[var(--color-border)]',
          'rounded-xl shadow-2xl flex flex-col',
          'max-w-[90vw] max-h-[85vh]',
        )}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)] shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="material-symbols-outlined text-lg text-[var(--color-text-muted)]">
              {mode === 'image' ? 'image' : 'description'}
            </span>
            <h2 className="text-sm font-medium text-[var(--color-text)] truncate">
              {fileName}
            </h2>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={handleOpenInDefaultApp}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              title="Open with system default app"
            >
              <span className="material-symbols-outlined text-sm">open_in_new</span>
              Open
            </button>
            <button
              onClick={handleCopyPath}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              title="Copy absolute file path"
            >
              <span className="material-symbols-outlined text-sm">
                {copyFeedback ? 'check' : 'content_copy'}
              </span>
              {copyFeedback ? 'Copied!' : 'Copy Path'}
            </button>
            <button
              onClick={onClose}
              className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
              aria-label="Close"
            >
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden flex flex-col p-4 min-h-0">
          {renderContent()}
        </div>
      </div>
    </div>
  );
}
