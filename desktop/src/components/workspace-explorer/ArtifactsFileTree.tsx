import { useState, useCallback } from 'react';

interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: TreeNode[];
}

export interface ArtifactsFileTreeProps {
  workspacePath?: string;
  onFileSelect?: (path: string) => void;
}

/**
 * Static folder structure for workspace filesystem browsing.
 * Requirements: 3.10, 9.11
 */
const WORKSPACE_FOLDERS: TreeNode[] = [
  {
    name: 'Artifacts',
    path: 'Artifacts',
    type: 'directory',
    children: [
      { name: 'Plans', path: 'Artifacts/Plans', type: 'directory' },
      { name: 'Reports', path: 'Artifacts/Reports', type: 'directory' },
      { name: 'Docs', path: 'Artifacts/Docs', type: 'directory' },
      { name: 'Decisions', path: 'Artifacts/Decisions', type: 'directory' },
    ],
  },
  { name: 'ContextFiles', path: 'ContextFiles', type: 'directory' },
  { name: 'Transcripts', path: 'Transcripts', type: 'directory' },
];

/**
 * ArtifactsFileTree - Collapsible file tree for browsing workspace filesystem.
 * Requirements: 3.10, 9.11
 */
export default function ArtifactsFileTree({
  workspacePath,
  onFileSelect,
}: ArtifactsFileTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [treeExpanded, setTreeExpanded] = useState(false);

  const toggleNode = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const handleFileClick = useCallback(
    (node: TreeNode) => {
      if (node.type === 'directory') {
        toggleNode(node.path);
      } else if (workspacePath) {
        onFileSelect?.(`${workspacePath}/${node.path}`);
      }
    },
    [toggleNode, workspacePath, onFileSelect]
  );

  return (
    <div className="pl-9 pr-3 pb-1" data-testid="artifacts-file-tree">
      {/* Toggle for file tree sub-section */}
      <div
        className="flex items-center gap-1 px-2 py-1 text-xs rounded cursor-pointer text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors"
        onClick={() => setTreeExpanded(!treeExpanded)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter') setTreeExpanded(!treeExpanded);
        }}
        data-testid="file-tree-toggle"
      >
        <span
          className="text-[10px] transition-transform"
          style={{ transform: treeExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}
        >
          ▶
        </span>
        <span className="material-symbols-outlined text-xs">folder</span>
        <span>Browse Files</span>
      </div>

      {treeExpanded && (
        <div className="ml-2" role="tree" aria-label="Workspace files">
          {WORKSPACE_FOLDERS.map((node) => (
            <FileNode
              key={node.path}
              node={node}
              depth={0}
              expanded={expanded}
              onToggle={handleFileClick}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FileNode({
  node,
  depth,
  expanded,
  onToggle,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (node: TreeNode) => void;
}) {
  const isExpanded = expanded.has(node.path);
  const isDir = node.type === 'directory';

  return (
    <div>
      <div
        className="flex items-center gap-1 px-1 py-0.5 text-xs rounded cursor-pointer text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] transition-colors"
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
        onClick={() => onToggle(node)}
        role="treeitem"
        aria-expanded={isDir ? isExpanded : undefined}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onToggle(node);
        }}
        data-testid={`file-node-${node.path.replace(/\//g, '-')}`}
      >
        {isDir && (
          <span
            className="text-[10px] w-3 transition-transform"
            style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}
          >
            ▶
          </span>
        )}
        {!isDir && <span className="w-3" />}
        <span className="material-symbols-outlined text-xs">
          {isDir ? (isExpanded ? 'folder_open' : 'folder') : 'draft'}
        </span>
        <span className="truncate">{node.name}</span>
      </div>
      {isDir && isExpanded && node.children && (
        <div role="group">
          {node.children.map((child) => (
            <FileNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}
