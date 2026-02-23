import { useEffect, useRef, useState, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import mermaid from 'mermaid';
import hljs from 'highlight.js';
import { useTheme } from '../../contexts/ThemeContext';

interface MarkdownRendererProps {
  content: string;
  className?: string;
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

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const handleWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -ZOOM_WHEEL_STEP : ZOOM_WHEEL_STEP;
        setScale((prev) => Math.min(Math.max(MIN_ZOOM, prev + delta), MAX_ZOOM));
      }
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.addEventListener('wheel', handleWheel, { passive: false });
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.removeEventListener('wheel', handleWheel);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

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
          <span className="material-symbols-outlined text-primary">schema</span>
          Mermaid Diagram
        </span>
        <div className="flex items-center gap-2">
          {/* Zoom controls */}
          <div className="flex items-center gap-1 bg-[var(--color-hover)] rounded-lg px-1">
            <button
              onClick={handleZoomOut}
              className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="Zoom out"
            >
              <span className="material-symbols-outlined text-lg">remove</span>
            </button>
            <button
              onClick={handleResetZoom}
              className="px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] min-w-[50px] text-center"
              title="Reset zoom (100% = fit to screen)"
            >
              {Math.round(scale * 100)}%
            </button>
            <button
              onClick={handleZoomIn}
              className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
              title="Zoom in"
            >
              <span className="material-symbols-outlined text-lg">add</span>
            </button>
          </div>
          {/* Close button */}
          <button
            onClick={onClose}
            className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-hover)] hover:bg-[var(--color-border)] rounded-lg transition-colors"
            title="Close (Esc)"
          >
            <span className="material-symbols-outlined">close</span>
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
          Ctrl + Scroll to zoom | Click outside to close | Esc to close
        </span>
      </div>
    </div>
  );
});

// Mermaid diagram component
const MermaidDiagram = memo(function MermaidDiagram({ chart }: { chart: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const { resolvedTheme } = useTheme();

  // Reinitialize mermaid when theme changes
  useEffect(() => {
    mermaid.initialize({
      startOnLoad: false,
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
      fontFamily: 'Space Grotesk, sans-serif',
    });
  }, [resolvedTheme]);

  useEffect(() => {
    const renderDiagram = async () => {
      if (!chart.trim()) return;

      try {
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
        const { svg: renderedSvg } = await mermaid.render(id, chart);
        setSvg(renderedSvg);
        setError(null);
      } catch (err) {
        console.error('Mermaid rendering error:', err);
        setError(err instanceof Error ? err.message : 'Failed to render diagram');
      }
    };

    renderDiagram();
  }, [chart]);

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
          <span className="material-symbols-outlined text-sm">error</span>
          <span className="text-sm font-medium">Mermaid Diagram Error</span>
        </div>
        <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto">{error}</pre>
        <details className="mt-2">
          <summary className="text-xs text-[var(--color-text-muted)] cursor-pointer hover:text-[var(--color-text)]">Show source</summary>
          <pre className="text-xs text-[var(--color-text-muted)] mt-2 overflow-x-auto">{chart}</pre>
        </details>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="flex items-center justify-center p-4 my-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg">
        <span className="text-[var(--color-text-muted)] text-sm">Loading diagram...</span>
      </div>
    );
  }

  return (
    <>
      <div className="my-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden group">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)] border-b border-[var(--color-border)]">
          <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm">schema</span>
            Mermaid Diagram
          </span>
          <div className="flex items-center gap-1">
            {/* Zoom button */}
            <button
              onClick={() => setIsModalOpen(true)}
              className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-card)] hover:bg-[var(--color-border)] rounded transition-colors"
              title="View fullscreen"
            >
              <span className="material-symbols-outlined text-sm">fullscreen</span>
              Zoom
            </button>
            {/* Download SVG button */}
            <button
              onClick={handleDownloadSvg}
              className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-card)] hover:bg-[var(--color-border)] rounded transition-colors"
              title="Download as SVG"
            >
              <span className="material-symbols-outlined text-sm">download</span>
              SVG
            </button>
            {/* Download PNG button */}
            <button
              onClick={handleDownloadPng}
              disabled={isDownloading}
              className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-card)] hover:bg-[var(--color-border)] rounded transition-colors disabled:opacity-50"
              title="Download as PNG"
            >
              <span className="material-symbols-outlined text-sm">
                {isDownloading ? 'hourglass_empty' : 'image'}
              </span>
              PNG
            </button>
          </div>
        </div>
        {/* Diagram content */}
        <div
          ref={containerRef}
          className="p-4 overflow-x-auto flex justify-center"
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
const CodeBlock = memo(function CodeBlock({
  language,
  children,
}: {
  language?: string;
  children: string;
}) {
  const [copied, setCopied] = useState(false);
  const codeRef = useRef<HTMLElement>(null);
  const isMermaid = language === 'mermaid';

  // Apply syntax highlighting (only for non-mermaid code blocks)
  useEffect(() => {
    if (!isMermaid && codeRef.current && language) {
      // Reset previous highlighting
      codeRef.current.removeAttribute('data-highlighted');
      try {
        hljs.highlightElement(codeRef.current);
      } catch (err) {
        console.error('Highlight error:', err);
      }
    }
  }, [children, language, isMermaid]);

  // Render mermaid diagram
  if (isMermaid) {
    return <MermaidDiagram chart={children} />;
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <div className="relative my-4 bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden group">
      {/* Header with language label and copy button */}
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)] border-b border-[var(--color-border)]">
        <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
          {language || 'code'}
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] bg-[var(--color-card)] hover:bg-[var(--color-border)] rounded transition-colors"
        >
          <span className="material-symbols-outlined text-sm">
            {copied ? 'check' : 'content_copy'}
          </span>
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      {/* Code content with syntax highlighting */}
      <pre className="p-4 overflow-x-auto">
        <code
          ref={codeRef}
          className={`text-sm font-mono ${language ? `language-${language}` : ''}`}
        >
          {children}
        </code>
      </pre>
    </div>
  );
});

// Inline code component (supports multiline with whitespace-pre-wrap)
const InlineCode = memo(function InlineCode({ children }: { children: React.ReactNode }) {
  const content = String(children);
  const hasNewlines = content.includes('\n');

  return (
    <code className={`px-1.5 py-0.5 bg-[var(--color-card)] border border-[var(--color-border)] rounded text-sm text-primary font-mono ${hasNewlines ? 'whitespace-pre-wrap block my-2' : ''}`}>
      {children}
    </code>
  );
});

// Memoized markdown components to prevent unnecessary re-renders
// Using 'any' for props to avoid complex react-markdown type compatibility issues
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const markdownComponents: Record<string, React.ComponentType<any>> = {
  // Headers
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold text-[var(--color-text)] mt-6 mb-4 pb-2 border-b border-[var(--color-border)]">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-xl font-bold text-[var(--color-text)] mt-5 mb-3">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-lg font-semibold text-[var(--color-text)] mt-4 mb-2">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-base font-semibold text-[var(--color-text)] mt-3 mb-2">{children}</h4>
  ),
  h5: ({ children }) => (
    <h5 className="text-sm font-semibold text-[var(--color-text)] mt-2 mb-1">{children}</h5>
  ),
  h6: ({ children }) => (
    <h6 className="text-sm font-medium text-[var(--color-text-muted)] mt-2 mb-1">{children}</h6>
  ),

  // Paragraphs
  p: ({ children }) => <p className="text-[var(--color-text)] mb-4 leading-relaxed">{children}</p>,

  // Links
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-primary hover:text-primary-hover underline decoration-primary/50 hover:decoration-primary transition-colors"
    >
      {children}
    </a>
  ),

  // Lists
  ul: ({ children }) => (
    <ul className="list-disc list-inside mb-4 space-y-1 text-[var(--color-text)]">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-4 space-y-1 text-[var(--color-text)]">{children}</ol>
  ),
  li: ({ children }) => <li className="text-[var(--color-text)] leading-relaxed">{children}</li>,

  // Blockquote
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-primary pl-4 my-4 text-[var(--color-text-muted)] italic">
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
    <div className="overflow-x-auto my-4">
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
  hr: () => <hr className="my-6 border-[var(--color-border)]" />,

  // Images
  img: ({ src, alt }) => (
    <img
      src={src}
      alt={alt || ''}
      className="max-w-full h-auto my-4 rounded-lg border border-[var(--color-border)]"
    />
  ),

  // Strong/Bold
  strong: ({ children }) => <strong className="font-semibold text-[var(--color-text)]">{children}</strong>,

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
const remarkPlugins = [remarkGfm, remarkBreaks];

const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = '',
}: MarkdownRendererProps) {
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
});

export default MarkdownRenderer;
