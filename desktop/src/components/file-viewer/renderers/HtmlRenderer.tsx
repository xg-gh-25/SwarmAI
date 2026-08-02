/**
 * HtmlRenderer -- HTML viewer: inline sandboxed iframe (rendered) + source view.
 *
 * Modes:
 *   - Rendered (default): inline via <iframe sandbox="allow-scripts allow-popups"
 *     srcDoc={content}> — scripts RUN (charts/tabs work) but the frame is a
 *     null/opaque origin that cannot reach the parent (never combine allow-scripts
 *     WITH allow-same-origin). FilePreviewModal renders html-preview inline too
 *     (though script-inert), so inline render is a proven pattern here. A
 *     bottom-right "Open in browser" FALLBACK (opens /workspace/file/raw in the
 *     system browser) covers two residual risks the inline frame can't: (a) the
 *     packaged WebKit rendering a blank srcDoc frame — historically worried about,
 *     NOT yet verified in a packaged .app, so QA the build; (b) reports needing
 *     real same-origin network/fetch (blocked by the opaque-origin sandbox).
 *   - Source: raw HTML in a syntax-highlighted <pre><code> block.
 *   - Toggle button top-right switches [Source] / [Rendered]. Size via onStatusInfo.
 */
import { useState, useEffect } from 'react';
import { openExternal } from '../../../utils/openExternal';
import { getApiBaseUrl } from '../../../services/tauri';

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

  useEffect(() => {
    onStatusInfo?.({ customInfo: formatFileSize(fileSize) });
  }, [fileSize, onStatusInfo]);

  // Open the file in the system browser, which renders text/html natively and
  // process-isolated. Reuse the dynamic api base (dev=8000 / desktop=dynamic /
  // Hive=same-origin) — never hardcode host/port — and encode the path (paths
  // contain spaces/CJK). Same contract as EvalDashboard ReportsTab.
  const openInBrowser = () => {
    void openExternal(`${getApiBaseUrl()}/api/workspace/file/raw?path=${encodeURIComponent(filePath)}`);
  };

  if (!content) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-[var(--color-text-muted)]">
        No HTML content available.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full relative">
      {/* Toggle button */}
      <div className="absolute top-2 right-2 z-10">
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
        /* Inline render via a sandboxed iframe — same pattern FilePreviewModal
           already ships for html-preview (sandbox='allow-same-origin', srcDoc).
           An 'Open in browser' fallback sits bottom-right in case the packaged
           WebKit ever renders a blank frame (the historical worry) — the user
           always has an escape hatch to the fully-isolated system browser. */
        <div className="flex-1 relative min-h-0">
          {/* sandbox="allow-scripts" WITHOUT allow-same-origin: agent HTML
              reports rely on inline <script> (Chart.js/Plotly/D3, tabs) — scripts
              must run or the report renders dead. Omitting allow-same-origin puts
              the frame in a null/opaque origin so it CANNOT reach the parent
              (the dangerous combo is allow-scripts + allow-same-origin together).
              Same-origin fetch/XHR inside the report is blocked — the
              "Open in browser" fallback covers anything needing real network. */}
          <iframe
            sandbox="allow-scripts allow-popups"
            srcDoc={content}
            className="w-full h-full border-0 bg-white"
            title={fileName}
            data-testid="html-preview-iframe"
          />
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
