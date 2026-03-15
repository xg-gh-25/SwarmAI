/**
 * SectionedExplorer — adds a collapsible "Working Files" section above the main tree.
 *
 * Extracts git-changed files (modified, added, untracked) from the existing treeData
 * and renders them in a flat list at the top. The main VirtualizedTree remains below
 * for Knowledge/Projects/Attachments browsing.
 *
 * This is a wrapper — it does NOT replace VirtualizedTree. All existing tree behavior
 * (zones, virtualization, context menu, inline rename) is preserved.
 */

import { useState, useMemo } from 'react';
import { AutoSizer } from 'react-virtualized-auto-sizer';
import { useTreeData } from '../../contexts/ExplorerContext';
import VirtualizedTree from './VirtualizedTree';
import type { FileTreeItem } from './FileTreeNode';
import type { TreeNode } from '../../types';
import { fileIcon, fileIconColor, gitStatusBadge } from '../../utils/fileUtils';

/** Files/directories that are pinned at the bottom as system items. */
const SYSTEM_NAMES = new Set(['.context', '.claude', 'config.json', 'proactive_state.json']);

interface SectionedExplorerProps {
  onFileDoubleClick?: (node: FileTreeItem) => void;
  onAttachToChat?: (item: FileTreeItem) => void;
}

interface WorkingFile {
  node: TreeNode;
  parentName: string;
}

/** Recursively collect all files with a git status (modified, added, untracked).
 *  Excludes system files/directories (they're pinned at the bottom). */
function collectWorkingFiles(nodes: TreeNode[], parentName: string = ''): WorkingFile[] {
  const results: WorkingFile[] = [];
  for (const node of nodes) {
    // Skip system items entirely
    if (SYSTEM_NAMES.has(node.name)) continue;
    if (node.type === 'file' && node.gitStatus && ['modified', 'added', 'untracked'].includes(node.gitStatus)) {
      results.push({ node, parentName });
    }
    if (node.type === 'directory' && node.children) {
      results.push(...collectWorkingFiles(node.children, node.name));
    }
  }
  return results;
}

/** Collect top-level system files/directories. */
function collectSystemFiles(nodes: TreeNode[]): TreeNode[] {
  return nodes.filter((n) => SYSTEM_NAMES.has(n.name));
}

/** Collapsible section header with chevron, label, and optional badge. */
function SectionHeader({
  title,
  count,
  isOpen,
  onToggle,
  badgeColor = 'neutral',
}: {
  title: string;
  count: number;
  isOpen: boolean;
  onToggle: () => void;
  badgeColor?: 'green' | 'neutral';
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-1.5 px-3.5 py-1 text-[10px] font-semibold uppercase tracking-[0.8px] text-[var(--color-text-dim)] cursor-pointer hover:text-[var(--color-text-muted)] transition-colors border-t border-[var(--color-border)] first:border-t-0"
    >
      <span
        className="material-symbols-outlined text-[14px] transition-transform duration-150"
        style={{ transform: isOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }}
      >
        expand_more
      </span>
      {title}
      {count > 0 && (
        <span
          className={`ml-auto text-[9px] font-medium px-1.5 rounded-full ${
            badgeColor === 'green'
              ? 'bg-[rgba(63,185,80,0.12)] text-[#3fb950]'
              : 'bg-[var(--color-hover)] text-[var(--color-text-dim)]'
          }`}
        >
          {count}
        </span>
      )}
    </button>
  );
}

/** Single working file row. */
function WorkingFileItem({
  file,
  onDoubleClick,
}: {
  file: WorkingFile;
  onDoubleClick?: (node: FileTreeItem) => void;
}) {
  const { node, parentName } = file;
  const icon = node.type === 'directory' ? 'folder' : fileIcon(node.name);
  const iconColor = node.type === 'directory' ? 'var(--color-icon-folder)' : fileIconColor(node.name);
  const badge = node.gitStatus ? gitStatusBadge(node.gitStatus) : null;

  const handleDoubleClick = () => {
    if (onDoubleClick) {
      onDoubleClick({
        id: node.path,
        name: node.name,
        type: node.type,
        path: node.path,
        workspaceId: 'swarmws',
        workspaceName: 'SwarmWS',
        gitStatus: node.gitStatus as any,
      });
    }
  };

  return (
    <div
      className="flex items-center gap-1.5 px-3.5 py-[2px] cursor-pointer hover:bg-[var(--color-hover)] transition-colors text-[12px] border-l-2 border-l-[#3fb950]"
      style={{ paddingLeft: 20 }}
      onDoubleClick={handleDoubleClick}
    >
      <span className="material-symbols-outlined text-[14px] flex-shrink-0" style={{ color: iconColor }}>
        {icon}
      </span>
      <span className="truncate text-[var(--color-text)] font-medium" style={{ letterSpacing: '-0.01em' }}>
        {node.name}
      </span>
      {parentName && (
        <span className="text-[10px] font-mono text-[var(--color-text-dim)] truncate flex-shrink-0" style={{ maxWidth: 80 }}>
          {parentName}
        </span>
      )}
      {badge && (
        <span
          className="text-[9px] font-medium px-1 rounded-[3px] flex-shrink-0 ml-auto"
          style={{ color: badge.color, backgroundColor: badge.bg }}
        >
          {badge.label}
        </span>
      )}
    </div>
  );
}

/** Single system file row — pinned at bottom with reduced opacity. */
function SystemFileItem({ node }: { node: TreeNode }) {
  const icon = node.type === 'directory' ? 'folder' : 'settings';
  const iconColor = node.type === 'directory' ? 'var(--color-icon-folder)' : 'var(--color-text-dim)';
  return (
    <div
      className="flex items-center gap-1.5 px-3.5 py-[2px] text-[11px] opacity-50 hover:opacity-75 transition-opacity cursor-default"
    >
      <span className="material-symbols-outlined text-[13px] flex-shrink-0" style={{ color: iconColor }}>
        {icon}
      </span>
      <span className="truncate text-[var(--color-text-muted)]">
        {node.name}
      </span>
    </div>
  );
}

export default function SectionedExplorer({ onFileDoubleClick, onAttachToChat }: SectionedExplorerProps) {
  const { treeData } = useTreeData();
  const [workingOpen, setWorkingOpen] = useState(true);

  const workingFiles = useMemo(() => collectWorkingFiles(treeData), [treeData]);
  const systemFiles = useMemo(() => collectSystemFiles(treeData), [treeData]);

  return (
    <div className="flex flex-col h-full">
      {/* Working Files section — scrollable when list is large */}
      <div className="flex-shrink-0" style={{ maxHeight: '40%', display: 'flex', flexDirection: 'column' }}>
        <SectionHeader
          title="Working Files"
          count={workingFiles.length}
          isOpen={workingOpen}
          onToggle={() => setWorkingOpen(!workingOpen)}
          badgeColor="green"
        />
        {workingOpen && workingFiles.length > 0 && (
          <div className="pb-0.5 overflow-y-auto flex-1 min-h-0">
            {workingFiles.map((f) => (
              <WorkingFileItem key={f.node.path} file={f} onDoubleClick={onFileDoubleClick} />
            ))}
          </div>
        )}
        {workingOpen && workingFiles.length === 0 && (
          <div className="px-3.5 py-2 text-[10px] text-[var(--color-text-dim)] italic">
            No uncommitted changes
          </div>
        )}
      </div>

      {/* Main tree — fills remaining space */}
      <div className="flex-1 overflow-hidden min-h-0">
        <AutoSizer
          renderProp={({ height, width }) => {
            if (height === undefined || width === undefined) return null;
            return (
              <VirtualizedTree
                height={height}
                width={width}
                onFileDoubleClick={onFileDoubleClick}
                onAttachToChat={onAttachToChat}
              />
            );
          }}
        />
      </div>

      {/* System files pinned at bottom — reduced opacity */}
      {systemFiles.length > 0 && (
        <div className="flex-shrink-0 border-t border-[var(--color-border)] py-1">
          {systemFiles.map((node) => (
            <SystemFileItem key={node.path} node={node} />
          ))}
        </div>
      )}
    </div>
  );
}
