/**
 * HtmlRenderer -- HTML viewer: inline sandboxed iframe (rendered) + source view.
 *
 * Modes:
 *   - Rendered (default): inline via <iframe sandbox="allow-scripts allow-popups"
 *     src={rawUrl}> — real navigation to the raw endpoint (srcDoc renders BLANK in
 *     packaged WKWebView, falsified twice, commit 496bbd7c). scripts RUN
 *     (charts/tabs work) but the frame is a null/opaque origin that cannot reach the
 *     parent (never combine allow-scripts WITH allow-same-origin). A bottom-right
 *     "Open in browser" FALLBACK (opens /workspace/file/raw in the system browser)
 *     covers reports needing real same-origin network/fetch (blocked by the sandbox).
 *   - Source: raw HTML in a syntax-highlighted <pre><code> block.
 *   - Toggle button top-right switches [Source] / [Rendered]. Size via onStatusInfo.
 *
 * FIT-WIDTH (run_732236aa): agent HTML reports use fixed-width containers
 * (width:1140/1180/1200px, wide tables) that force a HORIZONTAL scrollbar inside
 * the narrow Canvas panel (~320-900px). The rendered view has a Fit/Actual toggle
 * (default Fit):
 *   - FIT: the iframe sits in a SIZER div laid out at a fixed LOGICAL_WIDTH (1200px),
 *     which is transform:scale(s) where s = min(1, wrapperClientWidth / 1200). The
 *     sizer is position:absolute (out of flow, so its transform cannot feed back into
 *     the wrapper size — no ResizeObserver thrash) with an EXPLICIT height = H/s (H =
 *     wrapperClientHeight), so after scaling the visible box is exactly W × H and fills
 *     the panel; the iframe document lays out at 1200px and scrolls VERTICALLY inside
 *     (normal), with NO horizontal scroll. Content wider than 1200 still scrolls → use
 *     Actual / Open-in-browser.
 *   - ACTUAL: no transform, iframe width:100% height:100% (prior behavior — responsive
 *     reports reflow; fixed-width ones scroll as before).
 *
 * ⚠️ Measurement uses clientWidth/clientHeight (LAYOUT px), NEVER getBoundingClientRect:
 * the app applies a CSS zoom on <html> (useZoom.ts), and gBCR returns POST-zoom px →
 * scaling by a ratio of gBCR values double-counts the zoom (IMPROVEMENT.md:1596).
 * clientWidth is zoom-independent, so s is correct and the whole subtree then zooms
 * uniformly with the app. We transform a plain DIV (WKWebView-proven, MarkdownRenderer),
 * never the <iframe> element itself.
 */
import { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { openExternal } from '../../../utils/openExternal';
import { getApiBaseUrl } from '../../../services/tauri';

/** Logical desktop layout width the Fit mode lays the iframe out at, before scaling
 *  down to the panel. 1200 covers the observed report containers (1140/1180/1200). */
const LOGICAL_WIDTH = 1200;

interface RendererProps {
  filePath: string;
  fileName: string;
  content: string | null;
  encoding: 'utf-8' | 'base64';
  mimeType: string;
  fileSize: number;
  onStatusInfo?: (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Minimal HTML syntax highlighting for source view.
 * Wraps tags, attributes, strings, and comments with <span> for styling.
 */
function highlightHtml(html: string): string {
  return html
    // HTML comments
    .replace(
      /(&lt;!--[\s\S]*?--&gt;|<!--[\s\S]*?-->)/g,
      '<span style="color: var(--color-text-dim); font-style: italic;">$1</span>',
    )
    // Tags
    .replace(
      /(&lt;\/?)([\w-]+)/g,
      '<span style="color: #c678dd;">$1</span><span style="color: #e06c75;">$2</span>',
    )
    // Closing bracket
    .replace(
      /(\/?&gt;)/g,
      '<span style="color: #c678dd;">$1</span>',
    )
    // Attribute values (quoted strings)
    .replace(
      /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g,
      '<span style="color: #98c379;">$1</span>',
    )
    // Attribute names (word followed by =)
    .replace(
      /\b([\w-]+)(=)/g,
      '<span style="color: #d19a66;">$1</span><span style="color: var(--color-text-muted);">$2</span>',
    );
}

export default function HtmlRenderer({
  filePath,
  fileName,
  content,
  fileSize,
  onStatusInfo,
}: RendererProps) {
  const [mode, setMode] = useState<'rendered' | 'source'>('rendered');
  // Fit-width: default ON. Fit scales a sizer down to the panel width; Actual = 100%.
  const [fitMode, setFitMode] = useState(true);
  // Wrapper (measured) + the computed scale and the explicit sizer height (H/s).
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [fit, setFit] = useState<{ scale: number; frameH: number }>({ scale: 1, frameH: 0 });

  useEffect(() => {
    onStatusInfo?.({ customInfo: formatFileSize(fileSize) });
  }, [fileSize, onStatusInfo]);

  // Measure the wrapper (LAYOUT px, zoom-safe — see file docstring) and derive the
  // fit scale + sizer height. Runs synchronously (useLayoutEffect = no flash) and on
  // every panel resize via ResizeObserver.
  // Keyed on [mode]: the measured wrapper only mounts in RENDERED mode, so a
  // source→rendered re-entry remounts the wrapper — the effect must re-run to
  // re-measure + RE-ATTACH the observer (a [] dep would leave the re-entered view
  // with no resize tracking). In source mode wrapperRef is null → early return, no
  // observer (correct — nothing to measure).
  useLayoutEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const measure = () => {
      const W = el.clientWidth;
      const H = el.clientHeight;
      // 0-width trap (mid width-reveal animation / pre-layout): keep the last good
      // scale rather than collapsing to scale(0) which would vanish the iframe.
      if (W < 1) return;
      const s = Math.min(1, W / LOGICAL_WIDTH); // W>=1 ⇒ s ≥ 1/1200 > 0 (never 0)
      // Explicit sizer height = H/s so that after scale(s) the visible box is exactly
      // W × H and fills the panel; the iframe document scrolls vertically inside.
      const frameH = H / s;
      // Change-detecting: the observer keeps firing on resize in BOTH modes (kept
      // alive so `fit` is already-correct on an Actual→Fit toggle-back — no observer
      // teardown per toggle). Bail on an unchanged measurement so a same-size resize
      // event (or an Actual-mode resize the sizer doesn't consume) does NOT rerender.
      setFit((prev) => (prev.scale === s && prev.frameH === frameH ? prev : { scale: s, frameH }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [mode]);

  // The backend raw endpoint URL for this file. Serves Content-Type: text/html
  // with NO Content-Disposition:attachment (verified live) → a browser/WebView
  // navigates + renders it as a normal document. Reuse the dynamic api base
  // (dev=8000 / desktop=dynamic / Hive=same-origin) — never hardcode host/port —
  // and encode the path (paths contain spaces/CJK). Same URL drives BOTH the inline
  // iframe (src=) and the open-in-browser fallback.
  const rawUrl = `${getApiBaseUrl()}/api/workspace/file/raw?path=${encodeURIComponent(filePath)}`;

  // Open the fully-isolated system browser (escape hatch for anything needing
  // real same-origin network, which the opaque-origin iframe sandbox blocks).
  const openInBrowser = () => {
    void openExternal(rawUrl);
  };

  if (!content) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-[var(--color-text-muted)]">
        No HTML content available.
      </div>
    );
  }

  return (
    // `contain: paint` (run_2daacd0f, 6th-recurrence Canvas-open chat-input lag) gives
    // WebKit paint/composite isolation for THIS surface — the composite-heavy part of an
    // open Canvas: an opaque-origin iframe rendering an agent HTML report + a
    // transform:scale sizer (FIT mode). On WKWebView (this app is Tauri, not Electron) a
    // keystroke repaint in the sibling chat column could otherwise force re-compositing
    // this enlarged surface. `paint` lets the compositor skip this subtree on an
    // ancestor/sibling repaint. Scoped HERE (not the whole Canvas content column) on
    // purpose: paint containment makes the box a containing block for `position:fixed`
    // descendants, and the column also hosts FileEditorCore's fixed popover/modal — so
    // column-level paint would re-anchor + clip those (a REVIEW finding). This renderer
    // has NO fixed/portaled descendants (only a self-anchored `absolute` toolbar), and
    // its root is already `h-full w-full relative` + the iframe fills it → paint's clip
    // is visually inert. UNVERIFIED by profiling (both documented lag mechanisms are
    // already closed on modern WebKit — user chose to act on the composite hypothesis);
    // trivially revertible. If lag persists, PROFILE before attempt #7.
    <div className="flex flex-col h-full w-full relative" style={{ contain: 'paint' }} data-testid="html-renderer-root">
      {/* Toolbar (top-right): Fit/Actual (rendered mode only) + Source/Rendered. */}
      <div className="absolute top-2 right-2 z-10 flex items-center gap-1.5">
        {mode === 'rendered' && (
          <button
            onClick={() => setFitMode((f) => !f)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium
              text-[var(--color-text-muted)] hover:text-[var(--color-text)]
              bg-[var(--color-card)] border border-[var(--color-border)]
              hover:bg-[var(--color-hover)] transition-colors shadow-sm"
            title={fitMode ? 'Actual size (no fit-scaling)' : 'Fit width to panel'}
            aria-pressed={fitMode}
          >
            <span className="material-symbols-outlined text-sm">
              {fitMode ? 'width_normal' : 'fit_screen'}
            </span>
            {fitMode ? 'Actual' : 'Fit'}
          </button>
        )}
        <button
          onClick={() => setMode((prev) => (prev === 'rendered' ? 'source' : 'rendered'))}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium
            text-[var(--color-text-muted)] hover:text-[var(--color-text)]
            bg-[var(--color-card)] border border-[var(--color-border)]
            hover:bg-[var(--color-hover)] transition-colors shadow-sm"
          title={mode === 'rendered' ? 'Show HTML source' : 'Show rendered view'}
        >
          <span className="material-symbols-outlined text-sm">
            {mode === 'rendered' ? 'code' : 'visibility'}
          </span>
          {mode === 'rendered' ? 'Source' : 'Rendered'}
        </button>
      </div>

      {/* Content area */}
      {mode === 'rendered' ? (
        /* Inline render via REAL NAVIGATION: the iframe loads src={rawUrl} (the
           backend raw endpoint, Content-Type: text/html). srcDoc={content} rendered a
           BLANK frame in the packaged Tauri WKWebView (two prior fixes falsified,
           commit 496bbd7c). An 'Open in browser' fallback stays bottom-right.

           FIT mode wraps the iframe in a SIZER div laid out at LOGICAL_WIDTH and
           transform:scale(s)-ed down to the measured panel width (see file docstring).
           The wrapper (measured, overflow-hidden) clips the scaled box; the sizer is
           position:absolute so its transform can't feed back into the wrapper size. */
        <div
          ref={wrapperRef}
          className="flex-1 relative min-h-0 overflow-hidden"
          data-testid="html-fit-wrapper"
        >
          {/* sandbox="allow-scripts" WITHOUT allow-same-origin: agent HTML reports
              rely on inline <script> (Chart.js/Plotly/D3, tabs) — scripts must run.
              Omitting allow-same-origin forces a null/OPAQUE origin EVEN THOUGH
              src is same-origin as the app (MDN-verified) → the frame CANNOT reach
              the parent DOM/cookies/storage or make same-origin requests. The
              dangerous combo is allow-scripts + allow-same-origin together (lets the
              frame drop its own sandbox). Anything needing real network → the
              "Open in browser" fallback (fully-isolated system browser). */}
          {fitMode ? (
            <div
              data-testid="html-fit-sizer"
              className="absolute top-0 left-0"
              style={{
                width: `${LOGICAL_WIDTH}px`,
                height: `${fit.frameH}px`,
                transform: `scale(${fit.scale})`,
                transformOrigin: 'top left',
              }}
            >
              <iframe
                sandbox="allow-scripts allow-popups"
                src={rawUrl}
                className="w-full h-full border-0 bg-white"
                title={fileName}
                data-testid="html-preview-iframe"
              />
            </div>
          ) : (
            <iframe
              sandbox="allow-scripts allow-popups"
              src={rawUrl}
              className="w-full h-full border-0 bg-white"
              title={fileName}
              data-testid="html-preview-iframe"
            />
          )}
          <button
            onClick={openInBrowser}
            className="absolute bottom-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
              text-[var(--color-text)] bg-[var(--color-card)] border border-[var(--color-border)]
              hover:bg-[var(--color-hover)] transition-colors shadow-sm"
            title="Open the fully-rendered page in your system browser"
          >
            <span className="material-symbols-outlined text-sm">open_in_new</span>
            Open in browser
          </button>
        </div>
      ) : (
        <div className="flex-1 overflow-auto min-h-0 p-4">
          <pre
            className="text-xs leading-relaxed font-mono whitespace-pre-wrap break-words"
            style={{ color: 'var(--color-text)', tabSize: 2 }}
          >
            <code
              className="language-html hljs"
              dangerouslySetInnerHTML={{
                __html: highlightHtml(
                  content
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;'),
                ),
              }}
            />
          </pre>
        </div>
      )}
    </div>
  );
}
