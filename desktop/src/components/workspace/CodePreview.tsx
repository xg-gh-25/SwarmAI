import { useEffect, useRef, useState, memo } from 'react';
import hljs from 'highlight.js';
import clsx from 'clsx';

interface CodePreviewProps {
  content: string;
  filename: string;
  className?: string;
  showLineNumbers?: boolean;
}

// Map file extensions to highlight.js language identifiers
const getLanguageFromExtension = (filename: string): string => {
  const ext = filename.split('.').pop()?.toLowerCase();
  const langMap: Record<string, string> = {
    // JavaScript/TypeScript
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    // Python
    py: 'python',
    pyw: 'python',
    pyi: 'python',
    // Ruby
    rb: 'ruby',
    rake: 'ruby',
    gemspec: 'ruby',
    // Go
    go: 'go',
    // Rust
    rs: 'rust',
    // Java
    java: 'java',
    // C/C++
    c: 'c',
    h: 'c',
    cpp: 'cpp',
    hpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    // C#
    cs: 'csharp',
    // PHP
    php: 'php',
    // Swift
    swift: 'swift',
    // Kotlin
    kt: 'kotlin',
    kts: 'kotlin',
    // Scala
    scala: 'scala',
    // Shell
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    fish: 'bash',
    // Data formats
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    toml: 'ini',
    xml: 'xml',
    // Markup
    html: 'html',
    htm: 'html',
    xhtml: 'xml',
    // Stylesheets
    css: 'css',
    scss: 'scss',
    sass: 'scss',
    less: 'less',
    // SQL
    sql: 'sql',
    // Markdown
    md: 'markdown',
    mdx: 'markdown',
    // Config files
    ini: 'ini',
    cfg: 'ini',
    conf: 'ini',
    env: 'bash',
    // Other
    dockerfile: 'dockerfile',
    makefile: 'makefile',
    gradle: 'groovy',
    graphql: 'graphql',
    gql: 'graphql',
    prisma: 'prisma',
    vue: 'xml',
    svelte: 'xml',
  };

  // Check for special filenames
  const name = filename.toLowerCase();
  if (name === 'dockerfile') return 'dockerfile';
  if (name === 'makefile' || name === 'gnumakefile') return 'makefile';
  if (name.startsWith('.')) {
    // Dotfiles
    if (name === '.gitignore' || name === '.dockerignore') return 'bash';
    if (name === '.eslintrc' || name === '.prettierrc') return 'json';
  }

  return langMap[ext || ''] || 'plaintext';
};

export const CodePreview = memo(function CodePreview({
  content,
  filename,
  className,
  showLineNumbers = true,
}: CodePreviewProps) {
  const [copied, setCopied] = useState(false);
  const codeRef = useRef<HTMLElement>(null);
  const language = getLanguageFromExtension(filename);

  // Apply syntax highlighting
  useEffect(() => {
    if (codeRef.current && language !== 'plaintext') {
      // Reset previous highlighting
      codeRef.current.removeAttribute('data-highlighted');
      try {
        hljs.highlightElement(codeRef.current);
      } catch (err) {
        console.error('Highlight error:', err);
      }
    }
  }, [content, language]);

  // Handle copy
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  // Split content into lines for line numbers
  const lines = content.split('\n');

  return (
    <div className={clsx('flex flex-col bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden', className)}>
      {/* Header with language label and copy button */}
      <div className="flex items-center justify-between px-4 py-2 bg-[var(--color-hover)] border-b border-[var(--color-border)]">
        <span className="text-xs text-[var(--color-text-muted)] font-medium uppercase tracking-wider">
          {language}
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

      {/* Code content with optional line numbers */}
      <div className="flex overflow-x-auto">
        {showLineNumbers && (
          <div className="flex-shrink-0 py-4 pr-2 pl-4 text-right select-none bg-[var(--color-card)]/50 border-r border-[var(--color-border)]">
            {lines.map((_, index) => (
              <div
                key={index}
                className="text-xs text-[var(--color-text-muted)] font-mono leading-relaxed"
              >
                {index + 1}
              </div>
            ))}
          </div>
        )}
        <pre className="flex-1 p-4 overflow-x-auto">
          <code
            ref={codeRef}
            className={clsx(
              'text-sm font-mono leading-relaxed',
              language !== 'plaintext' && `language-${language}`
            )}
          >
            {content}
          </code>
        </pre>
      </div>
    </div>
  );
});

export default CodePreview;
