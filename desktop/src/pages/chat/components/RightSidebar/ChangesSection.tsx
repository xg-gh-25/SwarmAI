/**
 * ChangesSection — the ✍ "改动" section in the Radar sidebar (Run 2 redesign).
 *
 * Replaces ReferencedFilesSection. Shows ONLY files the agent WROTE/EDITED this
 * session (drops the read/searched noise), overlays a git-derived status badge —
 * NEW (untracked/added, 🟢) or UPD (modified, 🟡) — and drops the repeat-count.
 * The badge is authoritative git state (not tool-type), looked up from the
 * workspace tree's gitStatus. A file not present in the loaded tree (deep dir /
 * source-repo file the tree doesn't cover) shows no badge — it is NOT hidden
 * (never drop a file we can't classify).
 *
 * Click → dispatch swarm:open-file with { autoDiff: true } so the file opens in
 * the side FileViewer panel with the diff already shown (chat is not replaced).
 *
 * @exports ChangesSection
 * @exports gitStatusForPath — pure tree-lookup helper (unit-tested)
 */
import { memo, useCallback } from 'react';
import type { ReferencedFile, FileOperation } from '../../../../hooks/useReferencedFiles';
import type { TreeNode, GitStatus } from '../../../../types';
import { OPEN_FILE_EVENT } from '../../../../components/common/MarkdownRenderer';
import { useTreeData } from '../../../../contexts/ExplorerContext';
import { copyToClipboard } from '../../../../utils/clipboard';

interface ChangesSectionProps {
  grouped: Record<FileOperation, ReferencedFile[]>;
  totalCount: number;
}

/** Which badge (if any) a git status maps to. */
type Badge = 'new' | 'upd' | null;

function statusToBadge(status: GitStatus | undefined): Badge {
  if (status === 'untracked' || status === 'added') return 'new';
  if (status === 'modified' || status === 'renamed' || status === 'conflicting') return 'upd';
  return null; // deleted/ignored/unknown → no badge
}

/**
 * Find a file's git status by walking the workspace tree. Matches by path suffix
 * to be robust to format differences between the emitted reference path
 * (as-tool-emitted, may be relative or absolute) and TreeNode.path
 * (workspace-relative). Returns undefined if no file node matches — the caller
 * shows no badge (but still lists the file).
 */
export function gitStatusForPath(tree: TreeNode[], filePath: string): GitStatus | undefined {
  // Normalize to forward slashes; compare by trailing-segment overlap.
  const norm = filePath.replace(/\\/g, '/').replace(/^\.\//, '');
  let match: GitStatus | undefined;

  const walk = (nodes: TreeNode[]): boolean => {
    for (const node of nodes) {
      if (node.type === 'file') {
        const np = node.path.replace(/\\/g, '/');
        // Suffix match in either direction: the reference path may be a
        // workspace-relative subset of the tree path, or vice versa.
        if (np === norm || np.endsWith('/' + norm) || norm.endsWith('/' + np)) {
          match = node.gitStatus;
          return true; // first match wins
        }
      } else if (node.children) {
        if (walk(node.children)) return true;
      }
    }
    return false;
  };

  walk(tree);
  return match;
}

const BADGE_STYLE: Record<'new' | 'upd', { label: string; cls: string }> = {
  new: { label: 'NEW', cls: 'text-green-400 bg-green-400/10' },
  upd: { label: 'UPD', cls: 'text-yellow-500 bg-yellow-500/10' },
};

const FileRow = memo(function FileRow({ file, badge }: { file: ReferencedFile; badge: Badge }) {
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
      className="group flex items-center gap-1.5 px-2 py-1 cursor-pointer hover:bg-[var(--color-hover)] rounded text-[12px] transition-colors"
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

export function ChangesSection({ grouped }: ChangesSectionProps) {
  const { treeData } = useTreeData();
  const written = grouped.written; // written = created/edited this session; read/searched dropped

  if (written.length === 0) return null;

  return (
    <div className="py-1">
      {written.map((file) => (
        <FileRow key={file.path} file={file} badge={statusToBadge(gitStatusForPath(treeData, file.path))} />
      ))}
    </div>
  );
}
