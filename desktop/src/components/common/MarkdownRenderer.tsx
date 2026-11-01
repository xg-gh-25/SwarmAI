/**
 * MarkdownRenderer — renders chat / file-preview markdown (GFM, math, mermaid,
 * highlighted code) with SwarmAI's link, inline-code and image handling.
 *
 * Workspace file paths in `<a>` links and inline `<code>` spans are clickable and
 * dispatch OPEN_FILE_EVENT (`swarm:open-file`) on `document`. A link/path may carry a
 * `#base=<sha>[^]` fragment: the fragment is stripped before path detection, and a
 * valid sha ref is passed as `baseRef` (with the OUTPUTS-rail `autoDiff:false` shape)
 * so Canvas diffs the file against that baseline. Any other fragment is dropped.
 *
 * @exports default MarkdownRenderer — the memoized renderer component
 * @exports OPEN_FILE_EVENT — the open-file CustomEvent name
 * @exports isWorkspaceFilePath — clickable-file-path detector
 * @exports parseOpenFileTarget — split a link/path into URL-decoded path + validated baseRef
 * @exports buildOpenFileDetail — build the OPEN_FILE_EVENT detail for a link/path
 * @exports resolveImageSrc — local-only image src resolver (remote images dropped)
 */
/* eslint-disable react-refresh/only-export-components */
import { useContext, useEffect, useLayoutEffect, useRef, useState, useMemo, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import type { PluggableList } from 'unified';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import mermaid from 'mermaid';
import { useTranslation } from 'react-i18next';
import { useEscape } from '../../hooks/useLayer';
import hljs from 'highlight.js';
import { getApiBaseUrl } from '../../services/tauri';
import { useTheme } from '../../contexts/ThemeContext';
import { MarkdownLiveContext } from './markdownLive';
import { RemoteContentContext } from '../../pages/chat/remoteContentContext';
import { copyToClipboard } from '../../utils/clipboard';
import { openExternal } from '../../utils/openExternal';
import { FileTypeIcon } from './FileTypeIcon';
import { OFFICE_EXTENSIONS } from '../file-viewer/utils/fileViewTypes';

interface MarkdownRendererProps {
  content: string;
  className?: string;
  /** Workspace-relative directory path of the file being rendered — used to resolve relative image paths. */
  basePath?: string;
}

// Default SVG dimensions when not specified
const DEFAULT_SVG_WIDTH = 400;
const DEFAULT_SVG_HEIGHT = 300;
const DEFAULT_CANVAS_WIDTH = 800;
const DEFAULT_CANVAS_HEIGHT = 600;

// Zoom constraints
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;
const ZOOM_WHEEL_STEP = 0.1;

// PNG export scale factor for better quality
const PNG_SCALE_FACTOR = 2;

interface SvgDimensions {
  width: number;
  height: number;
}

/**
 * Extracts dimensions from an SVG element, checking width/height attributes and viewBox.
 * Returns default dimensions if none are found.
 */
function extractSvgDimensions(
  svgElement: SVGSVGElement,
  defaultWidth = DEFAULT_SVG_WIDTH,
  defaultHeight = DEFAULT_SVG_HEIGHT
): SvgDimensions {
  const widthAttr = svgElement.getAttribute('width');
  const heightAttr = svgElement.getAttribute('height');
  const viewBox = svgElement.getAttribute('viewBox');

  if (widthAttr && heightAttr) {
    return {
      width: parseFloat(widthAttr.replace(/[^0-9.]/g, '')) || defaultWidth,
      height: parseFloat(heightAttr.replace(/[^0-9.]/g, '')) || defaultHeight,
    };
  }

  if (viewBox) {
    const parts = viewBox.split(/\s+|,/);
    if (parts.length >= 4) {
      return {
        width: parseFloat(parts[2]) || defaultWidth,
        height: parseFloat(parts[3]) || defaultHeight,
      };
    }
  }

  return { width: defaultWidth, height: defaultHeight };
}

// Mermaid diagram modal for fullscreen view with zoom controls
const MermaidModal = memo(function MermaidModal({
  svg,
  isOpen,
  onClose,
}: {
  svg: string;
  isOpen: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [scale, setScale] = useState(1);
  const [scaledSvg, setScaledSvg] = useState(svg);
  const containerRef = useRef<HTMLDivElement>(null);

  // Viewport padding for modal content
  const VIEWPORT_HORIZONTAL_PADDING = 80;
  const VIEWPORT_VERTICAL_PADDING = 160;
  const VIEWPORT_FILL_RATIO = 0.95; // Fill 95% of available space

  // Process SVG to fill viewport when modal opens
  useEffect(() => {
    if (isOpen && svg) {
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = svg;
      const svgElement = tempDiv.querySelector('svg');

      if (svgElement) {
        const { width: origWidth, height: origHeight } = extractSvgDimensions(svgElement);

        // Calculate target size to fill most of the viewport
        const viewportWidth = window.innerWidth - VIEWPORT_HORIZONTAL_PADDING;
        const viewportHeight = window.innerHeight - VIEWPORT_VERTICAL_PADDING;

        // Calculate scale factor to fit viewport while maintaining aspect ratio
        const scaleX = viewportWidth / origWidth;
        const scaleY = viewportHeight / origHeight;
        const fitScale = Math.min(scaleX, scaleY) * VIEWPORT_FILL_RATIO;

        // Apply new dimensions directly to SVG
        const newWidth = Math.round(origWidth * fitScale);
        const newHeight = Math.round(origHeight * fitScale);

        svgElement.setAttribute('width', `${newWidth}px`);
        svgElement.setAttribute('height', `${newHeight}px`);

        // Ensure viewBox is set for proper scaling
        const viewBox = svgElement.getAttribute('viewBox');
        if (!viewBox) {
          svgElement.setAttribute('viewBox', `0 0 ${origWidth} ${origHeight}`);
        }

        setScaledSvg(tempDiv.innerHTML);
        setScale(1); // Reset scale since we've already scaled the SVG
      } else {
        setScaledSvg(svg);
        setScale(1);
      }
    }
  }, [isOpen, svg]);

  // The viewer is a layer: it takes Escape from the shared stack, so opened from a
  // dialog it closes itself, not the dialog under it.
  useEscape(isOpen, onClose);

  useEffect(() => {
    const handleWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -ZOOM_WHEEL_STEP : ZOOM_WHEEL_STEP;
        setScale((prev) => Math.min(Math.max(MIN_ZOOM, prev + delta), MAX_ZOOM));
      }
    };
    if (isOpen) {
      document.addEventListener('wheel', handleWheel, { passive: false });
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('wheel', handleWheel);
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  const handleZoomIn = () => setScale((prev) => Math.min(prev + ZOOM_STEP, MAX_ZOOM));
  const handleZoomOut = () => setScale((prev) => Math.max(prev - ZOOM_STEP, MIN_ZOOM));
  const handleResetZoom = () => setScale(1);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/90 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* Top toolbar */}
      <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-card)] border-b border-[var(--color-border)]">
        <span className="text-sm text-[var(--color-text)] font-medium flex items-center gap-2">
          <span className="material-symbols-outlined icon-md text-primary">schema</span>
          {t('ui.MarkdownRenderer.mermaidDiagram')}
        </span>
        <div className="flex items-center gap-2">
          {/* Zoom controls */}
          <div className="flex items-center gap-1 bg-[var(--color-hover)] rounded-lg px-1">
            <button
              onClick={handleZoomOut}
              className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title={t('ui.MarkdownRenderer.zoomOut')}
            >
              <span className="material-symbols-outlined icon-sm">remove</span>
            </button>
            <button
              onClick={handleResetZoom}
              className="px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-w-[50px] text-center"
              title={t('ui.MarkdownRenderer.resetZoom')}
            >
              {Math.round(scale * 100)}%
            </button>
            <button
              onClick={handleZoomIn}
              className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title={t('ui.MarkdownRenderer.zoomIn')}
            >
              <span className="material-symbols-outlined icon-sm">add</span>
            </button>
          </div>
          {/* Close button */}
          <button
            onClick={onClose}
            className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] hover:bg-[var(--color-border)] rounded-lg transition-colors"
            title={t('chat.input.closeEsc')}
          >
            <span className="material-symbols-outlined icon-md">close</span>
          </button>
        </div>
      </div>

      {/* Diagram area - scrollable */}
      <div
        ref={containerRef}
        className="flex-1 overflow-auto flex items-center justify-center p-4"
        onClick={(e) => e.target === e.currentTarget && onClose()}
      >
        <div
          className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl p-4 shadow-2xl transition-transform duration-150"
          style={{ transform: `scale(${scale})`, transformOrigin: 'center center' }}
          dangerouslySetInnerHTML={{ __html: scaledSvg }}
        />
      </div>

      {/* Bottom hint */}
      <div className="px-4 py-2 bg-[var(--color-card)] border-t border-[var(--color-border)] text-center">
        <span className="text-xs text-[var(--color-text-muted)]">
          {t('ui.MarkdownRenderer.modalHint')}
        </span>
      </div>
    </div>
  );
});

// Rendered diagrams by theme + source. A block that remounts (a turn's answer moving
// into its RESULT zone, a tab re-opening) shows its SVG on the first frame instead of
// flashing "Loading diagram..." while mermaid renders the same source again. Bounded:
// a streaming diagram renders every partial source, so the oldest entries go first.
/** The app's sans stack, read from --font-family-sans (index.css is the one definition).
 *  The fallback only applies where the variable cannot be read (tests without the CSS). */
const SANS_FALLBACK = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Segoe UI', sans-serif";
function appSansStack(): string {
  if (typeof document === 'undefined') return SANS_FALLBACK;
  const v = getComputedStyle(document.documentElement).getPropertyValue('--font-family-sans').trim();
  return v || SANS_FALLBACK;
}
const MERMAID_CACHE_MAX = 64;
const mermaidSvgCache = new Map<string, string>();
function cacheMermaidSvg(key: string, svg: string): void {
  mermaidSvgCache.delete(key);
  mermaidSvgCache.set(key, svg);
  while (mermaidSvgCache.size > MERMAID_CACHE_MAX) {
    const oldest = mermaidSvgCache.keys().next().value;
    if (oldest === undefined) break;
    mermaidSvgCache.delete(oldest);
  }
}

// Mermaid diagram component
const MermaidDiagram = memo(function MermaidDiagram({ chart }: { chart: string }) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const { resolvedTheme } = useTheme();
  const cacheKey = `${resolvedTheme}|${chart}`;
  // Still streaming: a source that does not parse yet is unfinished, not broken.
  const live = useContext(MarkdownLiveContext);
  const [svg, setSvg] = useState<string>(() => mermaidSvgCache.get(cacheKey) ?? '');
  const [error, setError] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  // Reinitialize mermaid when theme changes
  useEffect(() => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: resolvedTheme === 'dark' ? 'dark' : 'default',
      themeVariables: resolvedTheme === 'dark' ? {
        primaryColor: '#2b6cee',
        primaryTextColor: '#ffffff',
        primaryBorderColor: '#3d4f6f',
        lineColor: '#9da6b9',
        secondaryColor: '#1a1f2e',
        tertiaryColor: '#101622',
        background: '#1a1f2e',
        mainBkg: '#1a1f2e',
        nodeBorder: '#3d4f6f',
        clusterBkg: '#101622',
        titleColor: '#ffffff',
        edgeLabelBackground: '#1a1f2e',
      } : {
        primaryColor: '#2b6cee',
        primaryTextColor: '#1e293b',
        primaryBorderColor: '#cbd5e1',
        lineColor: '#64748b',
        secondaryColor: '#f1f5f9',
        tertiaryColor: '#f8fafc',
        background: '#ffffff',
        mainBkg: '#ffffff',
        nodeBorder: '#cbd5e1',
        clusterBkg: '#f8fafc',
        titleColor: '#1e293b',
        edgeLabelBackground: '#ffffff',
      },
      // mermaid measures labels with a concrete stack (no CSS variables), so resolve
      // the app's single definition (--font-family-sans in index.css) at init.
      fontFamily: appSansStack(),
    });
  }, [resolvedTheme]);

  useEffect(() => {
    const renderDiagram = async () => {
      if (!chart.trim()) return;
      const cached = mermaidSvgCache.get(cacheKey);
      if (cached) {
        setSvg(cached);
        setError(null);
        return;
      }

      try {
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
        const { svg: renderedSvg } = await mermaid.render(id, chart);
        cacheMermaidSvg(cacheKey, renderedSvg);
        setSvg(renderedSvg);
        setError(null);
      } catch (err) {
        // Mid-stream the source is usually just incomplete: keep the last good
        // diagram (or the loading line) instead of flashing the error box on every
        // chunk. The stream's end re-runs this with `live` false and reports it then.
        if (live) return;
        console.error('Mermaid rendering error:', err);
        setError(err instanceof Error ? err.message : 'Failed to render diagram');
      }
    };

    renderDiagram();
  }, [chart, cacheKey, live]);

  // Download as SVG
  const handleDownloadSvg = () => {
    if (!svg) return;
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `mermaid-diagram-${Date.now()}.svg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  // Download as PNG
  const handleDownloadPng = async () => {
    if (!svg || isDownloading) return;
    setIsDownloading(true);

    try {
      // Create a temporary container to get SVG element
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = svg;
      const svgElement = tempDiv.querySelector('svg');

      if (!svgElement) {
        throw new Error('SVG element not found');
      }

      // Ensure SVG has xmlns attribute for proper rendering
      if (!svgElement.getAttribute('xmlns')) {
        svgElement.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      }

      // Get dimensions from SVG using helper function
      const { width, height } = extractSvgDimensions(
        svgElement,
        DEFAULT_CANVAS_WIDTH,
        DEFAULT_CANVAS_HEIGHT
      );

      // Set explicit dimensions on SVG for canvas rendering
      svgElement.setAttribute('width', String(width));
      svgElement.setAttribute('height', String(height));

      // Scale for better quality
      const canvas = document.createElement('canvas');
      canvas.width = width * PNG_SCALE_FACTOR;
      canvas.height = height * PNG_SCALE_FACTOR;

      const ctx = canvas.getContext('2d');
      if (!ctx) {
        throw new Error('Canvas context not available');
      }

      // Fill background with theme-appropriate color
      ctx.fillStyle = resolvedTheme === 'dark' ? '#1a1f2e' : '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.scale(PNG_SCALE_FACTOR, PNG_SCALE_FACTOR);

      // Convert SVG to data URL (more reliable than blob URL)
      const svgString = new XMLSerializer().serializeToString(svgElement);
      const encodedSvg = encodeURIComponent(svgString)
        .replace(/'/g, '%27')
        .replace(/"/g, '%22');
      const dataUrl = `data:image/svg+xml;charset=utf-8,${encodedSvg}`;

      // Create image and draw to canvas
      const img = new Image();
      img.crossOrigin = 'anonymous';

      await new Promise<void>((resolve, reject) => {
        img.onload = () => {
          try {
            ctx.drawImage(img, 0, 0, width, height);
            resolve();
          } catch (drawErr) {
            reject(drawErr);
          }
        };
        img.onerror = (err) => {
          console.error('Image load error:', err);
          reject(new Error('Failed to load SVG as image'));
        };
        img.src = dataUrl;
      });

      // Download PNG
      const pngUrl = canvas.toDataURL('image/png');
      const link = document.createElement('a');
      link.href = pngUrl;
      link.download = `mermaid-diagram-${Date.now()}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (err) {
      console.error('PNG download error:', err);
      // Fallback: alert user to use SVG download instead
      alert('PNG download failed. Please try downloading as SVG instead.');
    } finally {
      setIsDownloading(false);
    }
  };

  if (error) {
    return (
      <div className="bg-status-error/10 border border-status-error/30 rounded-lg p-4 my-4">
        <div className="flex items-center gap-2 text-status-error mb-2">
          <span className="material-symbols-outlined icon-xs">error</span>
          <span className="text-sm font-medium">{t('ui.MarkdownRenderer.mermaidError')}</span>
        </div>
        <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto">{error}</pre>
        <details className="mt-2">
          <summary className="text-xs text-[var(--color-text-muted)] cursor-pointer hover:text-[var(--color-text)]">{t('ui.MarkdownRenderer.showSource')}</summary>
          <pre className="text-xs text-[var(--color-text-muted)] mt-2 overflow-x-auto">{chart}</pre>
        </details>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="flex items-center justify-center p-4 my-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg">
        <span className="text-[var(--color-text-muted)] text-sm">{t('ui.MarkdownRenderer.loadingDiagram')}</span>
      </div>
    );
  }

  return (
    <>
      <div className="my-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden group">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)] border-b border-[var(--color-border)]">
          <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider flex items-center gap-1.5">
            <span className="material-symbols-outlined icon-xs">schema</span>
            {t('ui.MarkdownRenderer.mermaidDiagram')}
          </span>
          <div className="flex items-center gap-1">
            {/* Zoom button */}
            <button
              onClick={() => setIsModalOpen(true)}
              className="btn-sm btn-secondary transition-colors"
              title={t('ui.MarkdownRenderer.viewFullscreen')}
            >
              <span className="material-symbols-outlined icon-xs">fullscreen</span>
              {t('ui.MarkdownRenderer.zoom')}
            </button>
            {/* Download SVG button */}
            <button
              onClick={handleDownloadSvg}
              className="btn-sm btn-secondary transition-colors"
              title={t('ui.MarkdownRenderer.downloadSvg')}
            >
              <span className="material-symbols-outlined icon-xs">download</span>
              {t('ui.MarkdownRenderer.svg')}
            </button>
            {/* Download PNG button */}
            <button
              onClick={handleDownloadPng}
              disabled={isDownloading}
              className="btn-sm btn-secondary transition-colors disabled:opacity-50"
              title={t('ui.MarkdownRenderer.downloadPng')}
            >
              <span className="material-symbols-outlined icon-xs">
                {isDownloading ? 'hourglass_empty' : 'image'}
              </span>
              {t('ui.MarkdownRenderer.png')}
            </button>
          </div>
        </div>
        {/* Diagram content */}
        <div
          ref={containerRef}
          className="p-4 overflow-x-auto flex justify-center-safe"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>

      {/* Fullscreen Modal */}
      <MermaidModal
        svg={svg}
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
      />
    </>
  );
});

// Code block component with syntax highlighting and copy button
// Above this a re-highlight could cost a visible frame; such a block recolours at idle.
const SYNC_HIGHLIGHT_MAX_CHARS = 20_000;

// Blocks already coloured in this app session, by a hash of language + text. A block
// mounted AGAIN with the same text (the answer moving from WORKING into RESULT at the
// end of a turn, a tab coming back) is coloured before paint, so it never shows one
// plain frame; a block seen for the first time still colours at idle. Bounded LRU of
// short keys; a hash collision only picks sync over idle colouring, never wrong colour.
const COLOURED_MAX = 500;
const colouredBlocks = new Set<string>();
function blockKey(language: string, text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193);
  return `${language}|${text.length}|${(h >>> 0).toString(36)}`;
}
function rememberColoured(key: string): void {
  colouredBlocks.delete(key);
  colouredBlocks.add(key);
  if (colouredBlocks.size > COLOURED_MAX) colouredBlocks.delete(colouredBlocks.values().next().value as string);
}

const CodeBlock = memo(function CodeBlock({
  language,
  children,
}: {
  language?: string;
  children: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const codeRef = useRef<HTMLElement>(null);
  const isMermaid = language === 'mermaid';

  // Apply syntax highlighting (only for non-mermaid code blocks) — DEFERRED to
  // after first paint. hljs.highlightElement is a synchronous, per-code-block DOM
  // walk; running it inside a plain mount-effect makes it part of the first-paint
  // critical path, so opening a long session with many code blocks blocks the
  // visible screen behind N synchronous highlight passes. Highlighting is
  // COLOR-ONLY (adds <span> class markup, does not change layout/height), so
  // deferring it to idle cannot shift scroll position or move the re-pin — the
  // code block paints immediately as plain text and gains color a frame later.
  // requestIdleCallback runs it off the critical path; a setTimeout(0) fallback
  // covers environments without rIC (jsdom/older Safari). The scheduled handle is
  // cancelled on re-run/unmount so a fast content change never double-highlights
  // or fires against a torn-down node.
  // An UPDATE (a streamed block growing) is recoloured before paint: React rewrites the
  // block's text as plain, and an idle-time highlight would let that plain frame show
  // between two coloured ones, five times a second. The first paint stays deferred
  // (the long-session rationale above), as does any block too big to colour per frame.
  const highlightedRef = useRef(false);
  useLayoutEffect(() => {
    if (isMermaid || !language) return;
    const run = () => {
      const el = codeRef.current;
      if (!el) return;
      el.removeAttribute('data-highlighted'); // reset previous highlighting
      try {
        hljs.highlightElement(el);
        highlightedRef.current = true;
        rememberColoured(blockKey(language, children));
      } catch (err) {
        console.error('Highlight error:', err);
      }
    };
    const seenBefore = highlightedRef.current || colouredBlocks.has(blockKey(language, children));
    if (seenBefore && children.length <= SYNC_HIGHLIGHT_MAX_CHARS) {
      run();
      return;
    }
    const ric = (window as unknown as {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (h: number) => void;
    });
    if (typeof ric.requestIdleCallback === 'function') {
      const handle = ric.requestIdleCallback(run, { timeout: 500 });
      return () => ric.cancelIdleCallback?.(handle);
    }
    const t = setTimeout(run, 0);
    return () => clearTimeout(t);
  }, [children, language, isMermaid]);

  // Render mermaid diagram
  if (isMermaid) {
    return <MermaidDiagram chart={children} />;
  }

  const handleCopy = async () => {
    try {
      await copyToClipboard(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <div className="relative my-1.5 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden group">
      {/* Header with language label and copy button — matches mockup code-head */}
      <div className="flex items-center justify-between px-3 py-1 bg-[var(--color-hover)] border-b border-[var(--color-border)]">
        <span className="text-[11px] text-[var(--color-text-muted)] font-mono font-normal">
          {language || 'code'}
        </span>
        <button
          onClick={handleCopy}
          className="btn-sm btn-secondary transition-colors cursor-pointer"
        >
          <span className="material-symbols-outlined icon-xs">
            {copied ? 'check' : 'content_copy'}
          </span>
          {copied ? t('common.copy.copied') : t('common.copy.copy')}
        </button>
      </div>
      {/* Code content with syntax highlighting */}
      <pre className="px-3 py-2 overflow-x-auto">
        <code
          ref={codeRef}
          className={`text-[11.5px] leading-[1.55] font-mono ${language ? `language-${language}` : ''}`}
        >
          {children}
        </code>
      </pre>
    </div>
  );
});

/**
 * Custom event name dispatched when a clickable file path is clicked in chat.
 * ThreeColumnLayout listens for this to open the FileEditorModal.
 */
export const OPEN_FILE_EVENT = 'swarm:open-file';

/**
 * Extensions recognized as clickable file paths when appearing as bare filenames
 * (no directory separator). Hoisted to module scope — this set is checked on every
 * inline code span render and must not be re-allocated per call.
 */
const CLICKABLE_EXTENSIONS = new Set([
  '.py', '.ts', '.tsx', '.js', '.jsx', '.rs', '.go', '.java', '.c', '.cpp', '.h',
  '.rb', '.sh', '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.cfg', '.ini',
  '.conf', '.html', '.css', '.scss', '.xml', '.sql', '.kt', '.swift', '.r', '.lua',
  '.pl', '.php', '.dart', '.scala', '.zig', '.tf', '.hcl', '.proto', '.graphql',
  '.dockerfile', '.makefile', '.csv', '.log', '.env',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.pdf',
  // Office/OpenDocument: unioned from the OFFICE_EXTENSIONS SSOT (run_a3292e2f) so
  // this list never drifts from the viewer's classifier (it previously omitted
  // odt/ods/odp). This is a broad ALL-clickable-types set; office is one subset.
  ...[...OFFICE_EXTENSIONS].map((e) => `.${e}`),
]);

/**
 * Detects whether inline code text looks like a file path worth making clickable.
 *
 * Accepts:
 * - Relative paths: Knowledge/Notes/file.md, backend/core/agent.py
 * - Bare filenames with code extensions: tool_summarizer.py, ChatPage.tsx
 * - Absolute paths under user home dirs: /Users/.../swarmai/backend/core/foo.py
 *
 * Rejects: URLs, single words without extension, shell commands, package names,
 * system paths (/etc, /usr, /bin, /var, /tmp, /proc, /sys, /dev, /sbin).
 */
export function isWorkspaceFilePath(text: string): boolean {
  // Reject prose (3+ spaces = sentence, not a file path)
  if ((text.match(/\s/g) || []).length > 2) return false;
  // Spaced text must look like a real path (contain / AND file extension)
  if (/\s/.test(text) && !(/[/\\]/.test(text) && /\.\w{1,10}$/.test(text))) return false;
  // Not a URL
  if (/^https?:\/\//i.test(text)) return false;
  // No path traversal segments
  if (text.split('/').includes('..')) return false;
  // Must end with a file extension (1-10 chars after last dot)
  if (!/\.\w{1,10}$/.test(text)) return false;
  // No obviously non-path characters
  if (/[<>|"'`]/.test(text)) return false;
  // Absolute paths: only allow user home directories, reject system paths
  if (text.startsWith('/')) {
    if (!/^\/(?:Users|home)\//.test(text)) return false;
    return true;
  }
  // Accept relative paths with /
  if (text.includes('/')) return true;
  // Accept bare filenames with known extensions
  const dot = text.lastIndexOf('.');
  if (dot > 0) {
    const ext = text.slice(dot).toLowerCase();
    if (CLICKABLE_EXTENSIONS.has(ext)) return true;
  }
  return false;
}

/**
 * Diff-baseline ref shape accepted from a `#base=<ref>` fragment: a 7-40 char hex sha
 * with an optional trailing `^` (parent). Same shape backend/core/ui_actions
 * build_surface_events emits (`<sha>^`). Anything else (`HEAD`, `../x`, `;rm`) is
 * rejected, so model-written link text can never inject an arbitrary git ref.
 */
const BASE_REF_RE = /^[0-9a-fA-F]{7,40}\^?$/;

/** URL-decode a link path; a malformed %-escape (or a literal `%`) keeps it raw. */
function decodePath(path: string): string {
  try {
    return decodeURIComponent(path);
  } catch {
    return path;
  }
}

/**
 * Split a link href / inline-code path into the bare path and an optional validated
 * diff baseline. Any `#...` fragment is ALWAYS stripped from `path` (so the extension
 * check in isWorkspaceFilePath still sees `.py`, not `.py#base=…`). Only a fragment of
 * exactly `base=<ref>` whose URL-decoded ref (react-markdown encodes `^` as `%5E`)
 * matches BASE_REF_RE yields `baseRef`; every other fragment is ignored.
 *
 * The path itself is URL-decoded too: react-markdown percent-encodes link targets,
 * so `/Users/x/my file.md` and non-ASCII names arrive as `my%20file.md` / `%E6%8A…`.
 * The split on `#` happens BEFORE decoding (a `%23` in a filename stays in the path).
 * A malformed escape keeps the raw path. Callers run isWorkspaceFilePath on the
 * DECODED path, so an encoded `%2e%2e` segment is still rejected as traversal.
 */
export function parseOpenFileTarget(raw: string): { path: string; baseRef?: string } {
  const hash = raw.indexOf('#');
  if (hash < 0) return { path: decodePath(raw) };
  const path = decodePath(raw.slice(0, hash));
  const fragment = raw.slice(hash + 1);
  if (!fragment.startsWith('base=')) return { path };
  let ref: string;
  try {
    ref = decodeURIComponent(fragment.slice('base='.length));
  } catch {
    return { path }; // malformed %-escape → ignore the fragment
  }
  return BASE_REF_RE.test(ref) ? { path, baseRef: ref } : { path };
}

/**
 * Build the OPEN_FILE_EVENT detail for an already-detected workspace file target
 * (fragment-stripped by parseOpenFileTarget, leading `./` removed). Without a valid
 * baseRef the detail is exactly `{ path }` (unchanged legacy shape). With one it
 * mirrors CanvasOutputRail.outputRowOpenDetail — `{ path, autoDiff: false, baseRef }`
 * — so Canvas opens it like an OUTPUTS row: content view, with Show Changes diffing
 * against `baseRef` instead of HEAD. (Mirrored, not imported: CanvasOutputRail
 * imports this module, so importing back would be circular.)
 */
export function buildOpenFileDetail(
  rawTarget: string,
): { path: string; autoDiff?: boolean; baseRef?: string } {
  const { path, baseRef } = parseOpenFileTarget(rawTarget);
  const cleanPath = path.startsWith('./') ? path.slice(2) : path;
  return baseRef ? { path: cleanPath, autoDiff: false, baseRef } : { path: cleanPath };
}

// Inline code component (supports multiline with whitespace-pre-wrap)
// When content looks like a workspace file path, renders as a clickable link
// that dispatches OPEN_FILE_EVENT for ThreeColumnLayout to handle.
const InlineCode = memo(function InlineCode({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const content = String(children);
  const hasNewlines = content.includes('\n');
  // A Hive session's paths name files on the Hive: plain code there, never a link.
  const remote = useContext(RemoteContentContext);
  // Detect on the fragment-stripped path (a `#base=…` suffix breaks the ext check).
  const isFilePath = !remote && !hasNewlines && isWorkspaceFilePath(parseOpenFileTarget(content).path);

  if (isFilePath) {
    // Fragment stripped, leading ./ removed; baseRef added when `#base=<sha>` is valid.
    const detail = buildOpenFileDetail(content);
    const cleanPath = detail.path;

    const handleClick = (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { ...detail } }));
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { ...detail } }));
      }
    };

    return (
      <code
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        role="button"
        tabIndex={0}
        // Drawn as a link (design 2026-10-26-session-products.md §3.4): a file the
        // answer names is clicked to open, so it must not look like plain code.
        className="px-1 py-px bg-[var(--color-hover)] rounded-[3px] text-[11.5px] text-primary hover:text-primary-hover underline decoration-primary/50 hover:decoration-primary font-mono cursor-pointer transition-colors inline-flex items-center gap-1"
        title={t('ui.MarkdownRenderer.openInCanvas')}
      >
        {/* One file, one look: the same type icon as the Canvas OUTPUTS rail and
            the chat's FileChip, desaturated so a type never reads as a status. */}
        <FileTypeIcon name={cleanPath} size="xs" muted />
        {children}
      </code>
    );
  }

  return (
    <code className={`px-1 py-px bg-[var(--color-hover)] rounded-[3px] text-[11.5px] text-[var(--color-inline-code)] font-mono ${hasNewlines ? 'whitespace-pre-wrap block my-2' : ''}`}>
      {children}
    </code>
  );
});

/**
 * Resolves an image src to a backend API URL so local workspace images render
 * in the markdown preview. Handles relative paths (images/foo.png) resolved
 * against the workspace-relative basePath.
 *
 * SECURITY (root ⑤, run_5e604f45): a REMOTE http(s) image src is DROPPED (returns
 * undefined → React omits the <img src> attr → NO network fetch), closing a
 * tracking-pixel / data-exfil vector where a markdown `![](https://evil/x.png)` in
 * chat/requirement text made the CSP=null webview auto-GET an external URL on display
 * (no click). react-markdown v10 drops raw HTML (no rehype-raw), so `![](url)` → this
 * resolver → the img component is the SOLE image vector; dropping here covers BOTH
 * desktop and Hive/web. Only LOCAL images survive: an http(s) URL whose host is exactly
 * `localhost`/`127.0.0.1` (the daemon raw-file endpoint, any port), plus `data:`/`blob:`
 * (used by local/off-DOM render paths), plus relative/absolute workspace paths resolved
 * to the local API below. The Tauri CSP `img-src` is a desktop-only belt; this component
 * check is the load-bearing fix.
 *
 * Returns `undefined` (NOT '') for a blocked src — an empty-string src makes WebKit
 * self-fetch the page URL; `undefined` makes React omit the attribute entirely.
 */
export function resolveImageSrc(src: string | undefined, basePath?: string): string | undefined {
  if (!src) return src;

  // Data/blob URIs — local, pass through.
  if (/^(data:|blob:)/i.test(src)) return src;

  // Remote http(s): keep ONLY if the host is exactly localhost / 127.0.0.1 (the local
  // daemon, any port). Any other host is DROPPED. EXACT host match — never
  // startsWith/includes (else `https://localhost.evil.com` would leak). FAIL-CLOSED:
  // a malformed URL (`https:// x`) throws in `new URL()`; we DROP it rather than let
  // the throw bubble into the img render and blank the whole chat tab via the
  // variant="tab" ErrorBoundary (Gate-1 BLOCKING, run_5e604f45).
  if (/^https?:/i.test(src)) {
    try {
      const host = new URL(src).hostname;
      return host === 'localhost' || host === '127.0.0.1' ? src : undefined;
    } catch {
      return undefined; // malformed → drop (fail-closed)
    }
  }

  // Build workspace-relative path (relative or absolute workspace path — local only).
  let relativePath = src;
  if (!src.startsWith('/')) {
    if (basePath === undefined) return src; // no base context — can't resolve
    relativePath = basePath ? `${basePath.replace(/\/$/, '')}/${src}` : src;
  }

  // Serve via backend raw file endpoint
  const apiBase = getApiBaseUrl();
  return `${apiBase}/api/workspace/file/raw?path=${encodeURIComponent(relativePath)}`;
}

// Memoized markdown components to prevent unnecessary re-renders
// Using 'any' for props to avoid complex react-markdown type compatibility issues
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const baseMarkdownComponents: Record<string, React.ComponentType<any>> = {
  // Headers
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold text-[var(--color-text)] mt-4 mb-2 pb-1.5 border-b border-[var(--color-border)]">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-xl font-bold text-[var(--color-text)] mt-3 mb-2">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-lg font-semibold text-[var(--color-text)] mt-3 mb-1.5">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-base font-semibold text-[var(--color-text)] mt-2 mb-1">{children}</h4>
  ),
  h5: ({ children }) => (
    <h5 className="text-sm font-semibold text-[var(--color-text)] mt-2 mb-1">{children}</h5>
  ),
  h6: ({ children }) => (
    <h6 className="text-sm font-medium text-[var(--color-text-muted)] mt-1.5 mb-1">{children}</h6>
  ),

  // Paragraphs
  p: ({ children }) => <p className="text-[var(--color-text)] mb-2 leading-normal">{children}</p>,

  // Links — workspace file paths open in FileEditor, external URLs open in browser.
  // Detection reuses isWorkspaceFilePath() so `<a>` links and inline `<code>` paths
  // share the same open-file behavior (dispatch OPEN_FILE_EVENT → ThreeColumnLayout).
  //
  // IMPORTANT: External links render WITHOUT href to prevent native webview navigation.
  // Tauri webview can race React's e.preventDefault() and navigate away from the app,
  // causing a fullscreen loading state with no way to return. Defense-in-depth:
  // Layer 1 (Rust): on_navigation blocks external URLs at the webview level.
  // Layer 2 (React): no href on external links = nothing for the webview to navigate to.
  //
  // A `#base=<sha>^` fragment (E3) is stripped BEFORE detection — else the `.py#…` tail
  // fails the extension check and a modified-file link would be treated as external.
  a: ({ href, children }) => {
    const isExternal = href && !isWorkspaceFilePath(
      parseOpenFileTarget(href.startsWith('file://') ? href.replace('file://', '') : href).path,
    );
    return (
      <a
        // Only set href for workspace file paths — external URLs use onClick only
        href={isExternal ? undefined : href}
        // Show the URL on hover so users can see where the link goes
        title={isExternal ? href : undefined}
        role={isExternal ? 'link' : undefined}
        onClick={async (e) => {
          if (href) {
            e.preventDefault();
            try {
              const filePath = href.startsWith('file://') ? href.replace('file://', '') : href;
              if (isWorkspaceFilePath(parseOpenFileTarget(filePath).path)) {
                document.dispatchEvent(
                  new CustomEvent(OPEN_FILE_EVENT, { detail: buildOpenFileDetail(filePath) }),
                );
              } else {
                const url = new URL(href, window.location.href);
                if (!['http:', 'https:'].includes(url.protocol)) return;
                await openExternal(href);
              }
            } catch (err) {
              console.error('Failed to open link:', href, err);
            }
          }
        }}
        className="text-primary hover:text-primary-hover underline decoration-primary/50 hover:decoration-primary transition-colors cursor-pointer"
      >
        {children}
      </a>
    );
  },

  // Lists — use list-outside with pl-5 so wrapped text aligns under content, not the marker
  ul: ({ children }) => (
    <ul className="list-disc list-outside pl-5 mb-2 space-y-0.5 text-[var(--color-text)]">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-outside pl-5 mb-2 space-y-0.5 text-[var(--color-text)]">{children}</ol>
  ),
  li: ({ children }) => <li className="text-[var(--color-text)] leading-normal pl-0.5">{children}</li>,

  // Blockquote
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-primary pl-4 my-2 text-[var(--color-text-muted)] italic">
      {children}
    </blockquote>
  ),

  // Code blocks
  code: ({ className, children }) => {
    const match = /language-(\w+)/.exec(className || '');
    const isInline = !match && !className;
    const codeContent = String(children).replace(/\n$/, '');

    if (isInline) {
      return <InlineCode>{children}</InlineCode>;
    }

    return <CodeBlock language={match?.[1]}>{codeContent}</CodeBlock>;
  },

  // Pre tag (wrapper for code blocks) - passes through children directly
  pre: ({ children }) => children,

  // Tables
  table: ({ children }) => (
    <div className="overflow-x-auto my-2">
      <table className="min-w-full border border-[var(--color-border)] rounded-lg overflow-hidden">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[var(--color-hover)]">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-[var(--color-border)]">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-[var(--color-hover)] transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-sm font-semibold text-[var(--color-text)] border-b border-[var(--color-border)]">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="px-4 py-3 text-sm text-[var(--color-text-muted)]">{children}</td>,

  // Horizontal rule
  hr: () => <hr className="my-3 border-[var(--color-border)]" />,

  // Images — base version (CHAT path, basePath undefined). MUST route through
  // resolveImageSrc so a remote `![](https://evil/x.png)` is DROPPED (no external fetch).
  // Previously passed `src={src}` raw, which was the exfil bypass (root ⑤, run_5e604f45).
  img: ({ src, alt }) => (
    <img
      src={resolveImageSrc(src)}
      alt={alt || ''}
      className="max-w-full h-auto my-4 rounded-lg border border-[var(--color-border)]"
      loading="lazy"
    />
  ),

  // Strong/Bold
  strong: ({ children }) => <strong className="font-bold text-[var(--color-text)]">{children}</strong>,

  // Emphasis/Italic
  em: ({ children }) => <em className="italic">{children}</em>,

  // Strikethrough
  del: ({ children }) => <del className="line-through text-[var(--color-text-muted)]">{children}</del>,

  // Task list items (GFM)
  input: ({ checked }) => (
    <input
      type="checkbox"
      checked={checked}
      readOnly
      className="mr-2 rounded border-[var(--color-border)] bg-[var(--color-card)] text-primary focus:ring-primary"
    />
  ),
};

// remarkPlugins array - stable reference
// remarkBreaks converts single line breaks to <br>, preserving newlines in output
// remarkMath parses $inline$ and $$block$$ math expressions
const remarkPlugins: PluggableList = [remarkGfm, remarkBreaks, [remarkMath, { singleDollarTextMath: false }]];

// rehypePlugins array - stable reference
// rehypeKatex renders parsed math expressions using KaTeX
// strict: false suppresses warnings for Unicode text in math mode
const rehypePlugins: PluggableList = [[rehypeKatex, { strict: false }]];

// A Hive session's content (the Hive tab, design 2026-10-29-swarm-out-hive-session-tab
// §7): a link to a path renders as its text (the file is on the Hive, not here) and no
// image is loaded from this machine (its alt text shows instead). External http(s)
// links keep the base behaviour.
const BaseLink = baseMarkdownComponents.a;
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- same props type as baseMarkdownComponents
const remoteMarkdownComponents: Record<string, React.ComponentType<any>> = {
  ...baseMarkdownComponents,
  a: (props: { href?: string; children?: React.ReactNode }) => {
    const { href, children } = props;
    const filePath = href?.startsWith('file://') ? href.replace('file://', '') : href;
    if (!href || isWorkspaceFilePath(parseOpenFileTarget(filePath ?? '').path)) {
      return <span data-testid="remote-path-text">{children}</span>;
    }
    return <BaseLink {...props} />;
  },
  img: ({ alt }: { alt?: string }) => (alt ? <span className="text-[var(--color-text-muted)]">{alt}</span> : null),
};

const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = '',
  basePath,
}: MarkdownRendererProps) {
  const remote = useContext(RemoteContentContext);
  // When basePath is provided, override the img component to resolve local paths
  const components = useMemo(() => {
    if (remote) return remoteMarkdownComponents;
    if (basePath === undefined) return baseMarkdownComponents;
    return {
      ...baseMarkdownComponents,
      img: ({ src, alt }: { src?: string; alt?: string }) => (
        <img
          src={resolveImageSrc(src, basePath)}
          alt={alt || ''}
          className="max-w-full h-auto my-4 rounded-lg border border-[var(--color-border)]"
          loading="lazy"
        />
      ),
    };
  }, [basePath, remote]);

  return (
    <div className={`markdown-content min-w-0 ${className}`}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
});

export default MarkdownRenderer;
