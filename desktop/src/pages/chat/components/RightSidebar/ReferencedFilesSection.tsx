/**
 * ReferencedFilesSection — Shows files the agent touched during this session.
 *
 * Grouped by operation (Written/Read/Searched), click to open, copy button for path.
 * Uses the useReferencedFiles hook for state management.
 */

import { memo, useCallback } from 'react';
import type { ReferencedFile, FileOperation } from '../../../../hooks/useReferencedFiles';
import { OPEN_FILE_EVENT } from '../../../../components/common/MarkdownRenderer';
import { copyToClipboard } from '../../../../utils/clipboard';

interface ReferencedFilesSectionProps {
  grouped: Record<FileOperation, ReferencedFile[]>;
  totalCount: number;
}

const OP_CONFIG: Record<FileOperation, { icon: string; label: string }> = {
  written: { icon: 'edit_note', label: 'Written' },
  read: { icon: 'description', label: 'Read' },
  searched: { icon: 'search', label: 'Searched' },
};

const FileRow = memo(function FileRow({ file }: { file: ReferencedFile }) {
  const handleClick = useCallback(() => {
    document.dispatchEvent(
      new CustomEvent(OPEN_FILE_EVENT, { detail: { path: file.path } }),
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
      <span className="flex-1 truncate text-[var(--color-text)]">
        {file.fileName}
        {file.count > 1 && (
          <span className="ml-1 text-[var(--color-text-muted)]">({file.count})</span>
        )}
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

function OperationGroup({ operation, files }: { operation: FileOperation; files: ReferencedFile[] }) {
  if (files.length === 0) return null;
  const { icon, label } = OP_CONFIG[operation];

  return (
    <div className="mb-1.5">
      <div className="flex items-center gap-1 px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] font-medium">
        <span className="material-symbols-outlined text-[11px]">{icon}</span>
        {label} ({files.length})
      </div>
      {files.map((file) => (
        <FileRow key={file.path} file={file} />
      ))}
    </div>
  );
}

export function ReferencedFilesSection({ grouped, totalCount }: ReferencedFilesSectionProps) {
  if (totalCount === 0) return null;

  return (
    <div className="py-1">
      <OperationGroup operation="written" files={grouped.written} />
      <OperationGroup operation="read" files={grouped.read} />
      <OperationGroup operation="searched" files={grouped.searched} />
    </div>
  );
}
