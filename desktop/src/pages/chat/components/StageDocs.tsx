/**
 * What a completed pipeline stage hands back in the chat (XG 2026-10-20): its one-line
 * summary, and a card per run document it wrote — REQUIREMENTS / DESIGN_SPEC / REVIEW /
 * FIXING_SPEC / REPORT (backend/core/run_docs.py).
 *
 * A card starts as one compact document row. "Show full text" reads the file from
 * the workspace (GET /workspace/file) and renders it in place; while that read is in
 * flight, the backend excerpt is a fast preview. "Open" puts it in the Canvas. The
 * row stays outside the stage fold so the produced document remains discoverable.
 *
 * @exports StageResult, DocCard, readRunDoc
 */
import { useContext, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
// Same value as MarkdownRenderer.OPEN_FILE_EVENT, not imported from there: the test
// setup imports TurnZones (and so this module), and MarkdownRenderer would pull
// mermaid / katex / the Tauri client into every test file before its own mocks.
// The Canvas test below pins the two together.
export const OPEN_FILE_EVENT = 'swarm:open-file';
import type { PipelineDocRef } from '../../../types';
import { tf } from '../tf';
import { ReadOnlyContext } from '../readOnlyContext';
import { docMeta } from '../runDocMeta';

type RenderText = (text: string, key: string) => ReactNode;

/** A run document's text, or null (unreadable, binary, gone). The API client is
 *  loaded on first use: TurnZones (and so this module) is imported by the test setup,
 *  and a static import would pull the HTTP client into every test before its mocks. */
export async function readRunDoc(path: string): Promise<string | null> {
  try {
    const { default: api } = await import('../../../services/api');
    const { data } = await api.get('/workspace/file', { params: { path } });
    const d = data as Record<string, unknown>;
    return d.encoding === 'utf-8' && typeof d.content === 'string' ? d.content : null;
  } catch {
    return null;
  }
}

/** The excerpt without its own section heading (the card names the document). */
function excerptBody(excerpt: string): string {
  return excerpt.replace(/^#{1,6} [^\n]*\n?/, '').trim();
}

export function DocCard({ doc, renderText }: { doc: PipelineDocRef; renderText: RenderText }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [state, setState] = useState<'idle' | 'loading' | 'failed'>('idle');
  const meta = docMeta(t, doc.name);
  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (text !== null) return;
    setState('loading');
    const got = await readRunDoc(doc.absolutePath);
    if (got === null) { setState('failed'); return; }
    setText(got);
    setState('idle');
  };
  const openInCanvas = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: doc.absolutePath } }));
  const body = doc.excerpt ? excerptBody(doc.excerpt) : '';
  const btn = 'btn-sm btn-ghost focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50';
  return (
    <div data-testid="stage-doc" data-name={doc.name} data-open={open ? 'true' : 'false'} className="border-l-2 border-[var(--color-border)] pl-3">
      <div className="flex items-baseline gap-2 min-w-0">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm leading-none self-center shrink-0 text-[var(--color-text-muted)]">{meta.icon}</span>
        <span className="shrink-0 font-mono text-[12.5px] text-[var(--color-text)]">{doc.name}</span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--color-text-muted)]">{meta.purpose}</span>
        <button type="button" data-testid="stage-doc-toggle" aria-expanded={open} onClick={toggle} className={btn}>
          {open ? tf(t, 'chat.turn.doc.collapse', 'Collapse') : tf(t, 'chat.turn.doc.expand', 'Show full text')}
        </button>
        {!readOnly && (
          <button type="button" data-testid="stage-doc-open" onClick={openInCanvas} title={doc.absolutePath} className={btn}>
            {tf(t, 'chat.turn.doc.open', 'Open')}
          </button>
        )}
      </div>
      {open && (
        <div data-testid="stage-doc-full" className="mt-2 text-[13px]">
          {state === 'loading' && body && (
            <div data-testid="stage-doc-loading-preview" className="max-h-[5.5em] overflow-hidden text-[12.5px] text-[var(--color-text-secondary)]">
              {renderText(body, `${doc.name}-loading`)}
            </div>
          )}
          {state === 'loading' && !body && <span className="text-[12px] text-[var(--color-text-dim)]">{tf(t, 'chat.turn.doc.loading', 'Loading…')}</span>}
          {state === 'failed' && <span className="text-[12px] text-[var(--color-text-dim)]">{tf(t, 'chat.turn.doc.failed', 'Could not read this document')}</span>}
          {state === 'idle' && text !== null && renderText(text, `${doc.name}-full`)}
        </div>
      )}
    </div>
  );
}

/** A completed stage's summary line and its document cards (nothing when it has neither). */
export function StageResult({ summary, docs, renderText }: {
  summary?: string | null; docs?: PipelineDocRef[]; renderText: RenderText;
}) {
  if (!summary && !docs?.length) return null;
  return (
    <div data-testid="stage-result" className="space-y-1.5">
      {summary && <div data-testid="stage-summary" className="text-[12.5px] text-[var(--color-text-secondary)] break-words">{summary}</div>}
      {docs?.map((d) => <DocCard key={d.absolutePath} doc={d} renderText={renderText} />)}
    </div>
  );
}
