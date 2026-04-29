/**
 * HtmlRenderer -- Sandboxed HTML preview with source-code toggle.
 *
 * Features:
 *   - Preview mode: sandboxed iframe with allow-same-origin, filling the panel
 *   - Source mode: raw HTML displayed in a syntax-highlighted <pre><code> block
 *   - Toggle button in the top-right corner to switch between [Preview] and [Source]
 *   - Reports file size via onStatusInfo
 */
import { useState, useEffect, useRef } from 'react';

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
  content,
  fileSize,
  onStatusInfo,
}: RendererProps) {
  const [mode, setMode] = useState<'preview' | 'source'>('preview');
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    onStatusInfo?.({ customInfo: formatFileSize(fileSize) });
  }, [fileSize, onStatusInfo]);

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
          onClick={() => setMode((prev) => (prev === 'preview' ? 'source' : 'preview'))}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium
            text-[var(--color-text-muted)] hover:text-[var(--color-text)]
            bg-[var(--color-card)] border border-[var(--color-border)]
            hover:bg-[var(--color-hover)] transition-colors shadow-sm"
          title={mode === 'preview' ? 'Show HTML source' : 'Show rendered preview'}
        >
          <span className="material-symbols-outlined text-sm">
            {mode === 'preview' ? 'code' : 'visibility'}
          </span>
          {mode === 'preview' ? 'Source' : 'Preview'}
        </button>
      </div>

      {/* Content area */}
      {mode === 'preview' ? (
        <iframe
          ref={iframeRef}
          sandbox="allow-same-origin"
          srcDoc={content}
          title="HTML Preview"
          className="flex-1 w-full border-0 bg-white rounded"
          style={{ minHeight: 0 }}
        />
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
