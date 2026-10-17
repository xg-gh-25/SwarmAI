/**
 * FileChip — one file, one look, everywhere in the chat (design-judgment Surface 6).
 *
 * A type icon (the same `fileIcon` / `fileIconColor` as the Canvas OUTPUTS rail,
 * desaturated so a red PDF never reads as a failure) + the file name; the full path
 * is the tooltip. Clicking opens it in Canvas; a file this turn changed opens on its
 * diff. Used by step rows, the folded work line, the "本轮改了" line and markdown
 * file references, so the same file is recognisable wherever it appears.
 *
 * @exports FileChip
 */
import type React from 'react';
import { useTranslation } from 'react-i18next';
import { OPEN_FILE_EVENT } from '../../../components/common/MarkdownRenderer';
import { fileIcon, fileIconColor } from '../../../utils/fileUtils';
import { tf } from '../tf';

/** Open `path` in Canvas; `diff` opens the change view (against HEAD). */
function openFileInCanvas(path: string, diff = false, e?: React.SyntheticEvent) {
  e?.preventDefault();
  e?.stopPropagation();
  document.dispatchEvent(new CustomEvent(OPEN_FILE_EVENT, {
    detail: diff ? { path, autoDiff: true } : { path },
  }));
}

const baseName = (p: string) => p.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || p;

export interface FileChipProps {
  /** Full path when known (clickable); otherwise only `name` is shown. */
  path?: string;
  /** Display name; defaults to the path's basename. */
  name?: string;
  /** This turn created / changed the file → a quiet tag, and the click opens the diff. */
  change?: 'new' | 'edited';
  /** Open on the diff without a tag (e.g. on an "Edited" step row). */
  diff?: boolean;
}

export function FileChip({ path, name, change, diff }: FileChipProps) {
  const { t } = useTranslation();
  const label = name ?? (path ? baseName(path) : '');
  const icon = (
    <span
      aria-hidden="true"
      className="material-symbols-outlined text-[14px] leading-none shrink-0"
      style={{ color: fileIconColor(label), filter: 'saturate(0.55)' }}
    >
      {fileIcon(label)}
    </span>
  );
  const tag = change && (
    <span className="ml-1 pl-1.5 border-l border-[var(--color-border)] text-[10.5px] text-[var(--color-text-muted)] font-sans shrink-0">
      {change === 'new' ? tf(t, 'chat.file.new', 'new') : tf(t, 'chat.file.edited', 'edited')}
    </span>
  );
  const body = (
    <>
      {icon}
      <span className="truncate min-w-0">{label}</span>
      {tag}
    </>
  );
  const cls = 'inline-flex items-center gap-1 max-w-full min-w-0 align-baseline px-1 py-px rounded-[4px] '
    + 'bg-[var(--color-hover)] font-mono text-[12px] text-[var(--color-text-secondary)]';
  if (!path) {
    return <span data-testid="file-chip" className={cls} title={label}>{body}</span>;
  }
  return (
    <button
      type="button"
      data-testid="file-chip"
      data-path={path}
      title={path}
      onClick={(e) => openFileInCanvas(path, !!change || !!diff, e)}
      className={`${cls} hover:text-[var(--color-text)] hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/50`}
    >
      {body}
    </button>
  );
}
