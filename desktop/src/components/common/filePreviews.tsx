/**
 * filePreviews — the ONE table of rendered previews an editable file can have
 * (docs/design/canvas-file-types.md P3). FileEditorCore shows Preview / Edit /
 * Changes; which files have a Preview, how it is drawn, and when a file is too large
 * to draw all come from here, so a new kind is one entry.
 *
 * - `previewKindOf(name)`   — 'markdown' | 'svg' | 'notebook' | 'mermaid' | null
 * - `canDrawPreview(kind, content)` — the per-kind size guard
 * - `PreviewBody`           — draws one kind (the caller owns the scroll box)
 * - `NotebookView`          — a Jupyter notebook: markdown cells, code cells with
 *   their outputs (stream, text, png/jpeg images, errors). HTML and JavaScript
 *   outputs are NOT rendered (untrusted): their text/plain fallback is shown.
 */
import { useMemo } from 'react';
import hljs from 'highlight.js';
import MarkdownRenderer from './MarkdownRenderer';
import { HIGHLIGHT_MAX_CHARS, shouldProcessSync } from './editorLimits';

export type PreviewKind = 'markdown' | 'svg' | 'notebook' | 'mermaid';

const BY_EXT: Record<string, PreviewKind> = {
  md: 'markdown', markdown: 'markdown', mdx: 'markdown',
  svg: 'svg',
  ipynb: 'notebook',
  mmd: 'mermaid', mermaid: 'mermaid',
};

export function previewKindOf(fileName: string): PreviewKind | null {
  const base = fileName.replace(/\\/g, '/').split('/').pop() ?? '';
  const i = base.lastIndexOf('.');
  return i < 0 ? null : BY_EXT[base.slice(i + 1).toLowerCase()] ?? null;
}

/** Notebooks carry their images as base64, so their size is mostly not text to
 *  parse: they get their own (larger) cap. */
const NOTEBOOK_MAX_CHARS = 20 * 1024 * 1024;

export function canDrawPreview(kind: PreviewKind, content: string): boolean {
  return kind === 'notebook' ? content.length <= NOTEBOOK_MAX_CHARS : shouldProcessSync(content.length);
}

/* ------------------------------ notebook ------------------------------ */

type Text = string | string[] | undefined;
const join = (t: Text) => (Array.isArray(t) ? t.join('') : t ?? '');
// eslint-disable-next-line no-control-regex
const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, '');

interface NbOutput {
  output_type: string;
  name?: string;
  text?: Text;
  data?: Record<string, Text>;
  ename?: string;
  evalue?: string;
  traceback?: string[];
}
interface NbCell { cell_type: string; source: Text; execution_count?: number | null; outputs?: NbOutput[] }

function Output({ o }: { o: NbOutput }) {
  if (o.output_type === 'stream') {
    return <pre className={`nb-out ${o.name === 'stderr' ? 'text-[var(--color-git-deleted,#f85149)]' : ''}`}>{join(o.text)}</pre>;
  }
  if (o.output_type === 'error') {
    return <pre className="nb-out text-[var(--color-git-deleted,#f85149)]" data-testid="nb-error">{stripAnsi((o.traceback ?? [`${o.ename}: ${o.evalue}`]).join('\n'))}</pre>;
  }
  const d = o.data ?? {};
  for (const mime of ['image/png', 'image/jpeg'] as const) {
    if (d[mime]) return <img className="nb-img" alt="output" src={`data:${mime};base64,${join(d[mime]).replace(/\s/g, '')}`} data-testid="nb-image" />;
  }
  if (d['text/markdown']) return <MarkdownRenderer content={join(d['text/markdown'])} />;
  const unsafe = d['text/html'] || d['application/javascript'];
  return (
    <>
      {unsafe && <div className="nb-note" data-testid="nb-html-skipped">HTML output (not rendered)</div>}
      {d['text/plain'] && <pre className="nb-out">{join(d['text/plain'])}</pre>}
    </>
  );
}

export function NotebookView({ content }: { content: string }) {
  const nb = useMemo(() => {
    try {
      const j = JSON.parse(content) as { cells?: NbCell[]; metadata?: { kernelspec?: { language?: string }; language_info?: { name?: string } } };
      if (!Array.isArray(j.cells)) return { error: 'no "cells" list' };
      return { cells: j.cells, lang: j.metadata?.language_info?.name || j.metadata?.kernelspec?.language || 'python' };
    } catch (e) {
      return { error: e instanceof Error ? e.message : String(e) };
    }
  }, [content]);

  if ('error' in nb) {
    return (
      <div className="nb-note" data-testid="nb-invalid">
        This notebook is not valid JSON ({nb.error}). Use Edit to see and fix the source.
      </div>
    );
  }
  const lang = hljs.getLanguage(nb.lang) ? nb.lang : 'plaintext';
  return (
    <div className="nb max-w-4xl mx-auto" data-testid="notebook-view">
      {nb.cells.map((c, i) => {
        const src = join(c.source);
        if (c.cell_type === 'markdown') return <div key={i} className="nb-cell" data-testid="nb-md"><MarkdownRenderer content={src} /></div>;
        if (c.cell_type !== 'code') return <pre key={i} className="nb-cell nb-out">{src}</pre>;
        const html = src.length <= HIGHLIGHT_MAX_CHARS ? hljs.highlight(src, { language: lang }).value : null;
        return (
          <div key={i} className="nb-cell" data-testid="nb-code">
            <div className="nb-prompt">In [{c.execution_count ?? ' '}]:</div>
            {html !== null
              // hljs output is escaped token markup of the cell's own source
              ? <pre className="nb-src hljs" dangerouslySetInnerHTML={{ __html: html }} />
              : <pre className="nb-src">{src}</pre>}
            {(c.outputs ?? []).map((o, k) => <Output key={k} o={o} />)}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------ one body ------------------------------ */

export function PreviewBody({ kind, content, basePath }: { kind: PreviewKind; content: string; basePath: string }) {
  if (kind === 'svg') {
    return (
      <div className="flex items-center justify-center h-full">
        <img
          className="max-w-full max-h-full w-auto h-auto"
          src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(content)}`}
          alt="SVG preview"
          data-testid="svg-preview"
        />
      </div>
    );
  }
  if (kind === 'notebook') return <NotebookView content={content} />;
  const md = kind === 'mermaid' ? `\`\`\`mermaid\n${content}\n\`\`\`` : content;
  return <MarkdownRenderer content={md} className="canvas-doc max-w-4xl mx-auto" basePath={basePath} />;
}
