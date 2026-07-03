/**
 * HtmlRenderer -- HTML viewer: open-in-browser (rendered) + in-app source view.
 *
 * ⚠️ Why NOT an in-app iframe preview: srcDoc rendering in the Tauri WebKit
 * (WKWebView) webview is unreliable — it renders a blank frame in the production
 * .app even for a valid static single-file document (Chrome/dev renders it fine,
 * so the bug is invisible until packaged). This is the SAME conclusion the Eval
 * report viewer already reached (see EvalDashboard ReportsTab: "srcDoc rendering
 * in the Tauri WebKit webview proved unreliable" → open in system browser). So
 * this renderer follows that established pattern instead of repeating the bug.
 *
 * Modes:
 *   - Rendered  (default): a card that opens the file in the system browser via
 *     the /workspace/file/raw endpoint (served as text/html — the browser renders
 *     it natively, fully process-isolated). This is the "real" rendered view.
 *   - Source: raw HTML in a syntax-highlighted <pre><code> block (in-app; works
 *     fine because it's plain <pre>, not an iframe).
 *   - Toggle button top-right switches [Source] / [Rendered].
 *   - Reports file size via onStatusInfo.
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
        <div className="flex-1 flex flex-col items-center justify-center gap-4 p-8 text-center">
          <span className="material-symbols-outlined text-5xl text-[var(--color-text-muted)]">language</span>
          <div>
            <p className="text-sm font-medium text-[var(--color-text)] mb-1">{fileName}</p>
            <p className="text-xs text-[var(--color-text-muted)] max-w-sm">
              HTML renders in your system browser — the in-app webview can't display it reliably.
              Click below to open the fully-rendered page, or switch to <strong>Source</strong> to read the markup here.
            </p>
          </div>
          <button
            onClick={openInBrowser}
            className="flex items-center gap-1.5 px-4 py-2 rounded-md text-sm font-medium
              text-white bg-[var(--color-accent)] hover:opacity-90 transition-opacity shadow-sm"
          >
            <span className="material-symbols-outlined text-base">open_in_new</span>
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
