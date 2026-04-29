/**
 * PdfRenderer — Renders PDF documents within the FileViewer.
 *
 * Uses react-pdf v10 to display PDF pages from base64-encoded content.
 * Supports page navigation, zoom controls (fit-width, fit-page, manual),
 * and reports page info via onStatusInfo.
 */

import { useState, useCallback, useRef, useEffect, memo } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Configure pdf.js worker for react-pdf v10
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

/** Zoom presets and constraints. */
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4.0;
const ZOOM_STEP = 0.25;
const DEFAULT_ZOOM_PCT = 100;

type ZoomMode = 'fit-width' | 'fit-page' | 'manual';

/**
 * PdfRenderer displays a PDF from base64-encoded content with page navigation
 * and zoom controls. It renders the current page plus one buffer page for
 * smoother scrolling.
 */
const PdfRenderer = memo(function PdfRenderer({
  content,
  fileName,
  onStatusInfo,
}: RendererProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [zoomMode, setZoomMode] = useState<ZoomMode>('fit-width');
  const [zoomPct, setZoomPct] = useState<number>(DEFAULT_ZOOM_PCT);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const containerRef = useRef<HTMLDivElement>(null);

  // Decode base64 content to Uint8Array for react-pdf
  const fileData = useCallback(() => {
    if (!content) return null;
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
  }, [content]);

  const pdfData = fileData();

  // Report page info to parent
  const reportPageInfo = useCallback(
    (page: number, total: number) => {
      onStatusInfo?.({ pageInfo: `Page ${page} / ${total}` });
    },
    [onStatusInfo],
  );

  /** Calculate pixel width for current zoom mode. */
  const getPageWidth = useCallback((): number | undefined => {
    const container = containerRef.current;
    if (!container) return undefined;
    const containerWidth = container.clientWidth - 48; // padding
    if (zoomMode === 'fit-width') return containerWidth;
    if (zoomMode === 'manual') return (containerWidth * zoomPct) / 100;
    // fit-page: handled via height prop, width auto
    return undefined;
  }, [zoomMode, zoomPct]);

  /** Calculate pixel height for fit-page mode. */
  const getPageHeight = useCallback((): number | undefined => {
    if (zoomMode !== 'fit-page') return undefined;
    const container = containerRef.current;
    if (!container) return undefined;
    return container.clientHeight - 80; // leave room for nav
  }, [zoomMode]);

  // --- Handlers ---

  const onDocumentLoadSuccess = useCallback(
    ({ numPages: total }: { numPages: number }) => {
      setNumPages(total);
      setCurrentPage(1);
      setLoading(false);
      setError(null);
      reportPageInfo(1, total);
    },
    [reportPageInfo],
  );

  const onDocumentLoadError = useCallback((err: Error) => {
    console.error('PDF load error:', err);
    setError(err.message || 'Failed to load PDF');
    setLoading(false);
  }, []);

  const goToPage = useCallback(
    (page: number) => {
      const clamped = Math.max(1, Math.min(page, numPages));
      setCurrentPage(clamped);
      reportPageInfo(clamped, numPages);
    },
    [numPages, reportPageInfo],
  );

  const prevPage = useCallback(() => goToPage(currentPage - 1), [currentPage, goToPage]);
  const nextPage = useCallback(() => goToPage(currentPage + 1), [currentPage, goToPage]);

  const handleFitWidth = useCallback(() => {
    setZoomMode('fit-width');
  }, []);

  const handleFitPage = useCallback(() => {
    setZoomMode('fit-page');
  }, []);

  const handleZoomIn = useCallback(() => {
    setZoomMode('manual');
    setZoomPct((prev) => Math.min(prev + ZOOM_STEP * 100, MAX_ZOOM * 100));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoomMode('manual');
    setZoomPct((prev) => Math.max(prev - ZOOM_STEP * 100, MIN_ZOOM * 100));
  }, []);

  const displayZoom = useCallback((): string => {
    if (zoomMode === 'fit-width') return 'Fit W';
    if (zoomMode === 'fit-page') return 'Fit P';
    return `${zoomPct}%`;
  }, [zoomMode, zoomPct]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        prevPage();
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        nextPage();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [prevPage, nextPage]);

  // --- No content ---
  if (!content || !pdfData) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[var(--color-text-muted)]">
        <span className="material-symbols-outlined text-4xl mb-2">picture_as_pdf</span>
        <p className="text-sm">No PDF content available</p>
      </div>
    );
  }

  // --- Error state ---
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-6 text-center">
        <span className="material-symbols-outlined text-4xl text-red-400 mb-3">error</span>
        <p className="text-sm font-medium text-[var(--color-text)] mb-1">
          Failed to render PDF
        </p>
        <p className="text-xs text-[var(--color-text-muted)] max-w-md">{error}</p>
        <p className="text-xs text-[var(--color-text-muted)] mt-2">{fileName}</p>
      </div>
    );
  }

  // Buffer page: render next page for smoother experience
  const bufferPage = currentPage < numPages ? currentPage + 1 : null;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-bg)] shrink-0">
        {/* Page navigation */}
        <div className="flex items-center gap-1">
          <button
            onClick={prevPage}
            disabled={currentPage <= 1}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Previous page"
          >
            <span className="material-symbols-outlined text-lg">chevron_left</span>
          </button>

          <span className="text-xs text-[var(--color-text-muted)] min-w-[80px] text-center select-none">
            {loading ? 'Loading...' : `Page ${currentPage} / ${numPages}`}
          </span>

          <button
            onClick={nextPage}
            disabled={currentPage >= numPages}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            title="Next page"
          >
            <span className="material-symbols-outlined text-lg">chevron_right</span>
          </button>
        </div>

        {/* Zoom controls */}
        <div className="flex items-center gap-1">
          <button
            onClick={handleFitWidth}
            className={`px-2 py-0.5 text-xs rounded transition-colors ${
              zoomMode === 'fit-width'
                ? 'bg-[var(--color-border)] text-[var(--color-text)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)]'
            }`}
            title="Fit width"
          >
            <span className="material-symbols-outlined text-base">width_normal</span>
          </button>

          <button
            onClick={handleFitPage}
            className={`px-2 py-0.5 text-xs rounded transition-colors ${
              zoomMode === 'fit-page'
                ? 'bg-[var(--color-border)] text-[var(--color-text)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)]'
            }`}
            title="Fit page"
          >
            <span className="material-symbols-outlined text-base">fit_page</span>
          </button>

          <div className="w-px h-4 bg-[var(--color-border)] mx-1" />

          <button
            onClick={handleZoomOut}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
            title="Zoom out"
          >
            <span className="material-symbols-outlined text-base">remove</span>
          </button>

          <span className="text-xs text-[var(--color-text-muted)] min-w-[44px] text-center select-none">
            {displayZoom()}
          </span>

          <button
            onClick={handleZoomIn}
            className="p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-border)] transition-colors"
            title="Zoom in"
          >
            <span className="material-symbols-outlined text-base">add</span>
          </button>
        </div>
      </div>

      {/* PDF content area */}
      <div
        ref={containerRef}
        className="flex-1 overflow-auto flex flex-col items-center py-4 px-6 bg-[var(--color-bg)]"
      >
        {loading && (
          <div className="flex items-center gap-2 text-[var(--color-text-muted)] py-12">
            <span className="material-symbols-outlined animate-spin text-xl">progress_activity</span>
            <span className="text-sm">Loading PDF...</span>
          </div>
        )}

        <Document
          file={{ data: pdfData }}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={null}
          className="flex flex-col items-center gap-4"
        >
          {/* Current page */}
          <div className="shadow-lg rounded border border-[var(--color-border)]">
            <Page
              pageNumber={currentPage}
              width={getPageWidth()}
              height={getPageHeight()}
              loading={
                <div className="flex items-center justify-center p-12 text-[var(--color-text-muted)]">
                  <span className="material-symbols-outlined animate-spin mr-2">progress_activity</span>
                  <span className="text-sm">Rendering page {currentPage}...</span>
                </div>
              }
            />
          </div>

          {/* Buffer page (next page, pre-rendered for smooth scroll) */}
          {bufferPage && (
            <div className="shadow-lg rounded border border-[var(--color-border)]">
              <Page
                pageNumber={bufferPage}
                width={getPageWidth()}
                height={getPageHeight()}
                loading={
                  <div className="flex items-center justify-center p-12 text-[var(--color-text-muted)]">
                    <span className="text-sm">Rendering page {bufferPage}...</span>
                  </div>
                }
              />
            </div>
          )}
        </Document>
      </div>
    </div>
  );
});

export default PdfRenderer;
