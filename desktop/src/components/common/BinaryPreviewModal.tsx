/**
 * Binary file preview modal for images, PDFs, and unsupported file types.
 *
 * This component is opened by ThreeColumnLayout when a user double-clicks
 * a non-text file in the Workspace Explorer. It fetches binary content from
 * the backend (`GET /workspace/file`) as base64 and renders it according
 * to the detected file type:
 *
 * - **Image mode** — `<img>` with data-URI, zoom (mouse-wheel) and pan (drag)
 * - **PDF mode**   — `react-pdf` Document + Page with vertical scroll
 * - **Unsupported** — file-name badge + "cannot preview" message
 *
 * Exports:
 * - ``BinaryPreviewModal``      — The modal React component
 * - ``BinaryPreviewModalProps`` — Props interface
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import clsx from 'clsx';
import api from '../../services/api';

// Configure pdf.js worker
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

export interface BinaryPreviewModalProps {
  isOpen: boolean;
  fileName: string;
  filePath: string;
  mode: 'image' | 'pdf' | 'unsupported';
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

  // Image viewer state
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [imageDimensions, setImageDimensions] = useState({ w: 0, h: 0 });

  // PDF state
  const [numPages, setNumPages] = useState<number>(0);

  const overlayRef = useRef<HTMLDivElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);

  // Fetch file content when modal opens
  const fetchContent = useCallback(async () => {
    setLoading(true);
    setError(null);
    setContent(null);
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setNumPages(0);
    setImageDimensions({ w: 0, h: 0 });
    try {
      const response = await api.get<FileResponse>('/workspace/file', {
        params: { path: filePath },
      });
      const data = response.data;
      setContent(data.content);
      // Handle both snake_case (backend) and camelCase (if transformed)
      const mime = data.mime_type ?? data.mimeType ?? 'application/octet-stream';
      setMimeType(mime);
      setFileSize(data.size ?? 0);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load file';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [filePath]);

  useEffect(() => {
    if (isOpen) {
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

  const handleRevealInFinder = useCallback(async () => {
    // Resolve absolute path by fetching workspace root
    try {
      const configResp = await api.get<{ file_path?: string; filePath?: string }>('/workspace');
      const wsRoot = configResp.data.file_path ?? configResp.data.filePath ?? '';
      const absolutePath = wsRoot ? `${wsRoot}/${filePath}` : filePath;

      const { open } = await import('@tauri-apps/plugin-shell');
      await open(absolutePath);
    } catch {
      // Fallback: try relative path or window.open
      try {
        const { open } = await import('@tauri-apps/plugin-shell');
        await open(filePath);
      } catch {
        window.open(filePath, '_blank');
      }
    }
  }, [filePath]);

  const handlePdfLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
  }, []);

  // Memoize PDF binary decode to avoid re-decoding on every render
  const pdfData = useMemo(() => {
    if (!content || mode !== 'pdf') return null;
    try {
      const binaryString = atob(content);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      return bytes;
    } catch {
      return null;
    }
  }, [content, mode]);

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

  const renderPdfMode = () => {
    if (!content) return null;
    if (!pdfData) {
      return (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <span className="material-symbols-outlined text-3xl text-red-400">error</span>
          <p className="text-sm text-red-400">Failed to decode PDF content</p>
          <button
            onClick={handleRevealInFinder}
            className="px-3 py-1.5 text-sm rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
          >
            Open in File Manager
          </button>
        </div>
      );
    }

    return (
      <div className="flex flex-col flex-1 overflow-hidden">
        {numPages > 0 && (
          <div className="text-xs text-[var(--color-text-muted)] text-center py-1 shrink-0">
            {numPages} {numPages === 1 ? 'page' : 'pages'}
          </div>
        )}
        <div
          className="flex-1 overflow-y-auto flex flex-col items-center gap-2 p-2"
          aria-label={`${fileName} PDF document`}
        >
          <Document
            file={{ data: pdfData }}
            onLoadSuccess={handlePdfLoadSuccess}
            onLoadError={() => setError('Failed to load PDF. The file may be corrupted or password-protected.')}
            loading={renderLoading()}
          >
            {Array.from({ length: numPages }, (_, i) => (
              <Page
                key={i + 1}
                pageNumber={i + 1}
                width={Math.min(800, window.innerWidth * 0.8)}
                className="shadow-md mb-2"
              />
            ))}
          </Document>
        </div>
      </div>
    );
  };

  const renderUnsupportedMode = () => {
    const ext = getExtension(fileName);
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-4">
        <span className="material-symbols-outlined text-5xl text-[var(--color-text-muted)]">
          description
        </span>
        <div className="text-center">
          <p className="text-sm font-medium text-[var(--color-text)] mb-1">{fileName}</p>
          {ext && (
            <span className="inline-block px-2 py-0.5 text-xs rounded bg-[var(--color-hover)] text-[var(--color-text-muted)] mb-3">
              .{ext.toLowerCase()}
            </span>
          )}
          <p
            className="text-sm text-[var(--color-text-muted)]"
            role="status"
            aria-live="polite"
          >
            This file type cannot be previewed
          </p>
        </div>
        <button
          onClick={handleRevealInFinder}
          className="px-4 py-2 text-sm rounded-lg bg-[var(--color-primary)] text-white hover:opacity-90 transition-opacity"
        >
          Open in File Manager
        </button>
      </div>
    );
  };

  const renderContent = () => {
    if (loading) return renderLoading();
    if (error) return renderError();
    switch (mode) {
      case 'image':
        return renderImageMode();
      case 'pdf':
        return renderPdfMode();
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
              {mode === 'image' ? 'image' : mode === 'pdf' ? 'picture_as_pdf' : 'description'}
            </span>
            <h2 className="text-sm font-medium text-[var(--color-text)] truncate">
              {fileName}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
            aria-label="Close"
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden flex flex-col p-4 min-h-0">
          {renderContent()}
        </div>
      </div>
    </div>
  );
}
