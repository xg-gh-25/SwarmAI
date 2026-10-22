/**
 * ChangesCard — "N files changed": what a turn changed in the code repos
 * (`turn_changes`, written by the backend from a change-tree snapshot taken when
 * the turn started — core/turn_changes.py). KiroCrew-style (XG 2026-10-19).
 *
 * One header line — the file count, then the turn's total +/- (the one figure to
 * read first). One row per file, biggest change first: a chevron when the change
 * can be shown as a diff, the file name (opens it in Canvas; a deleted file is plain text) with its
 * folder dimmed, `new` / `deleted` when that is what happened, then +a −d and a
 * five-square bar of the add/delete share. A file whose change is too large to show
 * says "File only"; a binary file says "binary". Rows separate with a hairline,
 * never boxes. Opening a row fetches its unified diff (the block holds the two
 * change trees, not the text — GET /api/chat/turn-diff) and shows it, lines
 * tinted by sign; once git has pruned the trees the row says so, and a failed
 * fetch (network) can be retried by opening the row again.
 *
 * Inert in a read-only preview (ReadOnlyContext): names do not open, diffs still
 * expand (reading is not acting).
 *
 * @exports ChangesCard, diffBar, totals
 */
import { useContext, useRef, useState } from 'react';
import { chatService } from '../../../services/chat';
import { useTranslation } from 'react-i18next';
import type { TurnChangeFile, TurnChangesContent } from '../../../types';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { fileIcon, fileIconColor } from '../../../utils/fileUtils';
import { tf } from '../tf';
import { ReadOnlyContext } from '../readOnlyContext';

const ADD = 'text-green-600 dark:text-green-400';
const DEL = 'text-red-600 dark:text-red-400';

/** The five squares: green / red share of the lines changed, grey for the rest when
 *  fewer than five lines changed. */
export function diffBar(added: number, deleted: number): Array<'add' | 'del' | 'none'> {
  const total = added + deleted;
  const filled = Math.min(5, total);
  const green = total ? Math.round((filled * added) / total) : 0;
  return Array.from({ length: 5 }, (_, i) => (i < green ? 'add' : i < filled ? 'del' : 'none'));
}

export function totals(files: TurnChangeFile[]): { added: number; deleted: number } {
  return files.reduce(
    (acc, f) => ({ added: acc.added + (f.added ?? 0), deleted: acc.deleted + (f.deleted ?? 0) }),
    { added: 0, deleted: 0 },
  );
}

function splitPath(p: string): { dir: string; name: string } {
  const i = p.lastIndexOf('/');
  return i < 0 ? { dir: '', name: p } : { dir: p.slice(0, i + 1), name: p.slice(i + 1) };
}

function DiffLines({ diff }: { diff: string }) {
  // The file header (diff --git / index / --- / +++) repeats the row; drop it.
  const lines = diff.split('\n');
  const start = lines.findIndex((l) => l.startsWith('@@'));
  const body = (start < 0 ? lines : lines.slice(start)).filter((l, i, a) => !(i === a.length - 1 && l === ''));
  return (
    <pre data-testid="change-diff" className="max-h-[480px] overflow-auto py-1 text-[12px] leading-[1.55] font-mono">
      {body.map((l, i) => {
        const cls = l.startsWith('@@')
          ? 'text-[var(--color-text-muted)]'
          : l.startsWith('+') ? `${ADD} bg-green-500/10`
            : l.startsWith('-') ? `${DEL} bg-red-500/10`
              : 'text-[var(--color-text-secondary)]';
        return <div key={i} className={`px-3 whitespace-pre ${cls}`}>{l || ' '}</div>;
      })}
    </pre>
  );
}

function ChangeRow({ file }: { file: TurnChangeFile }) {
  const { t } = useTranslation();
  const readOnly = useContext(ReadOnlyContext);
  const [open, setOpen] = useState(false);
  // undefined: not fetched yet; null: gone for good; 'error': failed, retry on reopen.
  const [diff, setDiff] = useState<string | null | undefined | { error: true }>(undefined);
  // Only the latest request may set the state (a stale one settling late must not
  // overwrite it); one in flight is not sent again.
  const req = useRef(0);
  const loading = useRef(false);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (!next || loading.current) return;
    if (diff !== undefined && !(typeof diff === 'object' && diff !== null)) return;
    const id = ++req.current;
    loading.current = true;
    setDiff(undefined);
    chatService.getTurnDiff({ gitRoot: file.gitRoot, start: file.start, end: file.end, path: file.path })
      .then((d) => { if (id === req.current) setDiff(d); })
      .catch(() => { if (id === req.current) setDiff({ error: true }); })
      .finally(() => { if (id === req.current) loading.current = false; });
  };
  const { dir, name } = splitPath(file.path);
  const canOpenFile = !readOnly && file.status !== 'deleted';
  const openFile = () => document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.absolutePath } }));
  const hasDiff = file.diffable && !file.binary;
  const tag = file.status === 'added'
    ? tf(t, 'chat.turn.changes.new', 'new')
    : file.status === 'deleted' ? tf(t, 'chat.turn.changes.deleted', 'deleted') : null;
  const nameEl = (
    <span className="min-w-0 truncate font-mono text-[12.5px]">
      {dir && <span className="text-[var(--color-text-muted)]">{dir}</span>}
      <span className="text-[var(--color-text)]">{name}</span>
    </span>
  );
  return (
    <li data-testid="change-row" data-path={file.path} className="border-t border-[var(--color-border)] first:border-t-0">
      <div className="flex items-center gap-2 px-3 py-1.5 min-w-0">
        {hasDiff ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={tf(t, 'chat.turn.changes.toggle', 'Show diff')}
            onClick={toggle}
            className="shrink-0 w-4 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <span aria-hidden="true" className={`material-symbols-outlined icon-sm leading-none transition-transform ${open ? 'rotate-90' : ''}`}>chevron_right</span>
          </button>
        ) : <span className="shrink-0 w-4" />}
        <span aria-hidden="true" className="material-symbols-outlined icon-sm leading-none shrink-0" style={{ color: fileIconColor(name), filter: 'saturate(0.55)' }}>
          {fileIcon(name)}
        </span>
        {canOpenFile ? (
          <button type="button" onClick={openFile} title={file.absolutePath} className="min-w-0 flex text-left hover:underline">
            {nameEl}
          </button>
        ) : <span title={file.absolutePath} className="min-w-0 flex">{nameEl}</span>}
        {tag && <span className="shrink-0 text-[11px] text-[var(--color-text-muted)]">{tag}</span>}
        <span className="ml-auto pl-3 shrink-0 flex items-center gap-2 tabular-nums text-[12px]">
          {file.binary ? (
            <span className="text-[var(--color-text-muted)]">{tf(t, 'chat.turn.changes.binary', 'binary')}</span>
          ) : (
            <>
              {!hasDiff && (
                <span data-testid="file-only" className="px-1.5 rounded-full border border-[var(--color-border)] text-[11px] text-[var(--color-text-muted)]">
                  {tf(t, 'chat.turn.changes.fileOnly', 'File only')}
                </span>
              )}
              {!!file.added && <span className={ADD}>+{file.added}</span>}
              {!!file.deleted && <span className={DEL}>−{file.deleted}</span>}
              <span aria-hidden="true" className="flex gap-[2px]">
                {diffBar(file.added ?? 0, file.deleted ?? 0).map((k, i) => (
                  <span key={i} className={`w-[7px] h-[7px] rounded-[1.5px] ${k === 'add' ? 'bg-green-500' : k === 'del' ? 'bg-red-500' : 'bg-[var(--color-border)]'}`} />
                ))}
              </span>
            </>
          )}
        </span>
      </div>
      {open && (
        <div className="border-t border-[var(--color-border)]">
          {diff === undefined ? (
            <p className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">{tf(t, 'chat.turn.changes.loading', 'Loading diff…')}</p>
          ) : diff === null ? (
            <p data-testid="diff-gone" className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.changes.gone', 'This diff is no longer available. Open the file instead.')}
            </p>
          ) : typeof diff === 'object' ? (
            <p data-testid="diff-error" className="px-3 py-1.5 text-[12px] text-[var(--color-text-muted)]">
              {tf(t, 'chat.turn.changes.failed', "Couldn't load the diff. Close and open the row to retry.")}
            </p>
          ) : <DiffLines diff={diff} />}
        </div>
      )}
    </li>
  );
}

/** `bare`: a section of the turn's output card (TurnCards OutputCard), no frame of its own. */
export function ChangesCard({ block, bare }: { block: TurnChangesContent; bare?: boolean }) {
  const { t } = useTranslation();
  const files = block.files;
  const sum = totals(files);
  const count = files.length + (block.omitted ?? 0);
  const anyFileOnly = files.some((f) => !f.diffable && !f.binary);
  return (
    <section data-testid="turn-changes" className={bare ? '' : 'rounded-lg border border-[var(--color-border)] overflow-hidden'}>
      <header className="flex items-center gap-2 px-3 py-2 border-b border-[var(--color-border)]">
        <span aria-hidden="true" className="material-symbols-outlined icon-sm leading-none text-[var(--color-text-muted)]">difference</span>
        <span className="font-mono text-[13px] text-[var(--color-text)]">
          {count === 1
            ? tf(t, 'chat.turn.changes.titleOne', '1 file changed')
            : tf(t, 'chat.turn.changes.title', '{{count}} files changed', { count })}
        </span>
        <span className="ml-auto flex gap-2 tabular-nums text-[12.5px]">
          {!!sum.added && <span className={ADD}>+{sum.added}</span>}
          {!!sum.deleted && <span className={DEL}>−{sum.deleted}</span>}
        </span>
      </header>
      {(anyFileOnly || !!block.omitted) && (
        <p className="px-3 py-1.5 border-b border-[var(--color-border)] text-[12px] italic text-[var(--color-text-muted)]">
          {block.omitted
            ? tf(t, 'chat.turn.changes.omitted', '{{n}} more files are not listed.', { n: block.omitted })
            : tf(t, 'chat.turn.changes.tooLarge', 'Some changes are too large to show as a diff. Click a file name to open it.')}
        </p>
      )}
      <ul>{files.map((f) => <ChangeRow key={f.absolutePath} file={f} />)}</ul>
    </section>
  );
}
