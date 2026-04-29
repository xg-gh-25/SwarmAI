/**
 * ImageRenderer -- Renders browser-compatible images (PNG, JPG, GIF, WebP, AVIF, BMP, ICO)
 * with zoom/pan controls ported from BinaryPreviewModal.
 *
 * Features:
 *   - Renders from base64 data-URI
 *   - Mouse-wheel zoom (0.1x -- 10x, step 0.1)
 *   - Click-drag panning when zoomed > 1x
 *   - Toolbar: Fit / 100% / Zoom In / Zoom Out + percentage display
 *   - Checkerboard background for transparency (PNG, WebP)
 *   - Reports natural dimensions via onStatusInfo on load
 */
import { useState, useRef, useCallback, useEffect } from 'react';

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

const MIN_ZOOM = 0.1;
const MAX_ZOOM = 10;
const ZOOM_STEP = 0.1;

export default function ImageRenderer({
  fileName,
  content,
  mimeType,
  onStatusInfo,
}: RendererProps) {
  const [scale, setScale] = useState(1);
  const [translate, setTranslate] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [fitMode, setFitMode] = useState(true);

  const containerRef = useRef<HTMLDivElement>(null);

  /* ------------------------------------------------------------------ */
  /*  Zoom helpers                                                       */
  /* ------------------------------------------------------------------ */

  const clampZoom = (val: number) => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, val));

  const resetToFit = useCallback(() => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setFitMode(true);
  }, []);

  const resetTo100 = useCallback(() => {
    setScale(1);
    setTranslate({ x: 0, y: 0 });
    setFitMode(false);
  }, []);

  const zoomIn = useCallback(() => {
    setScale((prev) => clampZoom(prev + ZOOM_STEP));
    setFitMode(false);
  }, []);

  const zoomOut = useCallback(() => {
    setScale((prev) => clampZoom(prev - ZOOM_STEP));
    setFitMode(false);
  }, []);

  /* ------------------------------------------------------------------ */
  /*  Mouse wheel zoom                                                   */
  /* ------------------------------------------------------------------ */

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setScale((prev) => clampZoom(prev + (e.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP)));
    setFitMode(false);
  }, []);

  /* ------------------------------------------------------------------ */
  /*  Click-drag pan (only when zoomed > 1x)                             */
  /* ------------------------------------------------------------------ */

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (scale <= 1 && fitMode) return;
      e.preventDefault();
      setIsDragging(true);
      setDragStart({ x: e.clientX - translate.x, y: e.clientY - translate.y });
    },
    [scale, fitMode, translate],
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

  /* ------------------------------------------------------------------ */
  /*  Image load -- report dimensions                                    */
  /* ------------------------------------------------------------------ */

  const handleImageLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      onStatusInfo?.({ dimensions: `${img.naturalWidth} × ${img.naturalHeight}` });
    },
    [onStatusInfo],
  );

  /* Reset view when content changes */
  useEffect(() => {
    resetToFit();
  }, [content, resetToFit]);

  if (!content) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-[var(--color-text-muted)]">
        No image data available.
      </div>
    );
  }

  const dataUri = `data:${mimeType};base64,${content}`;
  const showCheckerboard = /png|webp|avif|gif|svg/i.test(mimeType);
  const canPan = scale > 1 || !fitMode;

  return (
    <div className="flex flex-col h-full w-full">
      {/* Toolbar */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[var(--color-border)] shrink-0">
        <button
          onClick={resetToFit}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          title="Fit to panel"
        >
          <span className="material-symbols-outlined text-sm">fit_screen</span>
          Fit
        </button>
        <button
          onClick={resetTo100}
          className="flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          title="Actual size (100%)"
        >
          100%
        </button>

        <div className="w-px h-4 bg-[var(--color-border)] mx-1" />

        <button
          onClick={zoomOut}
          className="flex items-center justify-center w-6 h-6 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          title="Zoom out"
        >
          <span className="material-symbols-outlined text-sm">remove</span>
        </button>
        <span className="text-xs text-[var(--color-text-muted)] min-w-[3rem] text-center tabular-nums">
          {Math.round(scale * 100)}%
        </span>
        <button
          onClick={zoomIn}
          className="flex items-center justify-center w-6 h-6 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-hover)] transition-colors"
          title="Zoom in"
        >
          <span className="material-symbols-outlined text-sm">add</span>
        </button>
      </div>

      {/* Image canvas */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden flex items-center justify-center min-h-0"
        style={{
          cursor: canPan ? (isDragging ? 'grabbing' : 'grab') : 'default',
          /* Checkerboard pattern for transparency */
          ...(showCheckerboard
            ? {
                backgroundImage:
                  'linear-gradient(45deg, var(--color-hover) 25%, transparent 25%), ' +
                  'linear-gradient(-45deg, var(--color-hover) 25%, transparent 25%), ' +
                  'linear-gradient(45deg, transparent 75%, var(--color-hover) 75%), ' +
                  'linear-gradient(-45deg, transparent 75%, var(--color-hover) 75%)',
                backgroundSize: '20px 20px',
                backgroundPosition: '0 0, 0 10px, 10px -10px, -10px 0px',
              }
            : {}),
        }}
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
          draggable={false}
          style={{
            ...(fitMode
              ? { objectFit: 'contain' as const, maxWidth: '100%', maxHeight: '100%' }
              : {}),
            transform: `translate(${translate.x}px, ${translate.y}px) scale(${scale})`,
            transformOrigin: 'center center',
            transition: isDragging ? 'none' : 'transform 0.1s ease-out',
            userSelect: 'none',
          }}
        />
      </div>
    </div>
  );
}
