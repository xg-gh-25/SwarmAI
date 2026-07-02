/**
 * ChangesSection — the ✍ "Changes" section in the Radar sidebar.
 *
 * Replaces ReferencedFilesSection. Shows ONLY files the agent WROTE/EDITED this
 * session (drops the read/searched noise), overlays a git-derived status badge —
 * NEW (untracked, 🟢) or UPD (modified, 🟡) — and drops the repeat-count.
 *
 * Badge source (Run-B fix): `useChangeStatus`, which asks git directly per file
 * (resolve → committed) so it works for SOURCE-REPO files too — the earlier
 * tree-gitStatus lookup only covered the SwarmWS workspace, so files the agent
 * actually edits (in the source repo) got no badge. A file git can't classify
 * shows no badge (but is NOT hidden — never drop a file we can't classify).
 * NEW files sort before UPD.
 *
 * Click → dispatch swarm:open-file with { autoDiff: true } so the file opens in
 * the side FileViewer panel with the diff already shown (chat is not replaced).
 *
 * @exports ChangesSection
 */
import { memo, useCallback, useMemo } from 'react';
import type { ReferencedFile, FileOperation } from '../../../../hooks/useReferencedFiles';
import { OPEN_FILE_EVENT } from '../../../../components/common/MarkdownRenderer';
import { useChangeStatus, type ChangeStatus } from '../../../../hooks/useChangeStatus';
import { copyToClipboard } from '../../../../utils/clipboard';

interface ChangesSectionProps {
  grouped: Record<FileOperation, ReferencedFile[]>;
  totalCount: number;
}

const BADGE_STYLE: Record<ChangeStatus, { label: string; cls: string }> = {
  new: { label: 'NEW', cls: 'text-green-400 bg-green-400/10' },
  upd: { label: 'UPD', cls: 'text-yellow-500 bg-yellow-500/10' },
};

const FileRow = memo(function FileRow({ file, badge }: { file: ReferencedFile; badge: ChangeStatus | undefined }) {
  const handleClick = useCallback(() => {
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.path, autoDiff: true } }),
    );
  }, [file.path]);

  const handleCopy = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    await copyToClipboard(file.absolutePath || file.path);
  }, [file.absolutePath, file.path]);

  return (
    <div
      className="group flex h-6 items-center gap-1 px-2 cursor-pointer hover:bg-[var(--color-hover)] rounded text-[12px] transition-colors"
      onClick={handleClick}
      title={file.path}
    >
      {badge && (
        <span className={`shrink-0 rounded px-1 text-[9px] font-bold tracking-wide ${BADGE_STYLE[badge].cls}`}>
          {BADGE_STYLE[badge].label}
        </span>
      )}
      <span className="flex-1 truncate font-mono text-[var(--color-text)]">
        {file.fileName}
      </span>
      <button
        onClick={handleCopy}
        className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-all"
        title="Copy path"
        aria-label={`Copy path of ${file.fileName}`}
      >
        <span className="material-symbols-outlined text-[12px]">content_copy</span>
      </button>
    </div>
  );
});

/** Sort order: NEW first, then UPD, then unbadged; stable within a group. */
function badgeRank(s: ChangeStatus | undefined): number {
  return s === 'new' ? 0 : s === 'upd' ? 1 : 2;
}

export function ChangesSection({ grouped }: ChangesSectionProps) {
  const written = grouped.written; // written = created/edited this session; read/searched dropped
  const paths = useMemo(() => written.map((f) => f.path), [written]);
  const statusMap = useChangeStatus(paths);

  const ordered = useMemo(() => {
    return [...written].sort((a, b) => badgeRank(statusMap.get(a.path)) - badgeRank(statusMap.get(b.path)));
  }, [written, statusMap]);

  if (written.length === 0) return null;

  return (
    <div className="py-0.5">
      {ordered.map((file) => (
        <FileRow key={file.path} file={file} badge={statusMap.get(file.path)} />
      ))}
    </div>
  );
}
