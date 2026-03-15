import { ReactNode, useState, useCallback, useRef } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS, ModalType, useSessionMeta } from '../../contexts/LayoutContext';
import { ExplorerProvider, useTreeData } from '../../contexts/ExplorerContext';
import { WorkspaceExplorer } from '../workspace-explorer';
import { BottomBar } from './BottomBar';
import FileEditorModal from '../common/FileEditorModal';
import BinaryPreviewModal from '../common/BinaryPreviewModal';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';
import { classifyFileForPreview } from '../../utils/fileUtils';
import type { FilePreviewType } from '../../utils/fileUtils';
import SkillsModal from '../modals/SkillsModal';
import MCPSettingsModal from '../modals/MCPSettingsModal';
import AgentsModal from '../modals/AgentsModal';
import SettingsModal from '../modals/SettingsModal';
import WorkspacesModal from '../modals/WorkspacesModal';
import SwarmCoreModal from '../modals/SwarmCoreModal';
import WorkspaceSettingsModal from '../modals/WorkspaceSettingsModal';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import type { GitStatus } from '../../types';
import api from '../../services/api';

// Left sidebar width constant
const LEFT_SIDEBAR_WIDTH = LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH;

// Minimum width for main chat panel to ensure usability
const MIN_MAIN_CHAT_PANEL_WIDTH = 300;

interface ThreeColumnLayoutProps {
  children: ReactNode;
}

// TopBar -- Session context bar replacing the old file search.
// Shows: session topic, context usage %, attached files, active agent.
// Remains draggable for Tauri window move (macOS).
function TopBar() {
  const { activeSessionMeta } = useSessionMeta();

  const handleMouseDown = async (e: React.MouseEvent) => {
    if (e.button === 0 && e.clientX > 80) {
      try {
        await getCurrentWindow().startDragging();
      } catch (err) {
        console.error('Failed to start dragging:', err);
      }
    }
  };

  const meta = activeSessionMeta;
  const contextPct = meta?.contextPct ?? 0;
  const ringColor =
    contextPct > 80 ? 'text-red-400' : contextPct > 60 ? 'text-amber-400' : 'text-[var(--color-text-muted)]';

  return (
    <div
      onMouseDown={handleMouseDown}
      className="h-10 bg-[var(--color-bg-chrome)] border-b border-[var(--color-border)] flex-shrink-0 select-none cursor-default flex items-center"
      data-tauri-drag-region
      data-testid="top-bar"
    >
      {/* Spacer for macOS traffic lights */}
      <div className="w-20 flex-shrink-0" />

      {/* Session context info -- centered */}
      <div className="flex-1 flex items-center justify-center gap-3 text-[11px] text-[var(--color-text-muted)]" role="status" aria-label="Session context">
        {meta ? (
          <>
            <span className="flex items-center gap-1.5 text-[var(--color-text-secondary)] font-medium truncate min-w-0" style={{ maxWidth: 'clamp(120px, 25vw, 360px)', letterSpacing: '-0.02em' }} aria-label={`Session: ${meta.topic || 'New Session'}`}>
              <span className="material-symbols-outlined text-[14px]" aria-hidden="true">chat_bubble</span>
              {meta.topic || 'New Session'}
            </span>
            <div className="w-px h-3 bg-[var(--color-border)] flex-shrink-0" aria-hidden="true" />
            <span className={`flex items-center gap-1 ${ringColor}`} aria-label={`Context usage: ${meta.contextPct != null ? Math.round(meta.contextPct) + '%' : 'unknown'}`}>
              <span className="material-symbols-outlined text-[14px]" aria-hidden="true">memory</span>
              {meta.contextPct != null ? `${Math.round(meta.contextPct)}%` : '--'}
            </span>
            <div className="w-px h-3 bg-[var(--color-border)] flex-shrink-0" aria-hidden="true" />
            <span className="flex items-center gap-1" aria-label={`${meta.fileCount} attached files`}>
              <span className="material-symbols-outlined text-[14px]" aria-hidden="true">attach_file</span>
              {meta.fileCount}
            </span>
            <div className="w-px h-3 bg-[var(--color-border)] flex-shrink-0" aria-hidden="true" />
            <span className="flex items-center gap-1" aria-label={`Agent: ${meta.agentName}`}>
              <span className="material-symbols-outlined text-[14px]" aria-hidden="true">smart_toy</span>
              {meta.agentName}
            </span>
          </>
        ) : (
          <span className="text-[var(--color-text-dim)]">SwarmAI</span>
        )}
      </div>

      {/* Right spacer for symmetry */}
      <div className="w-20 flex-shrink-0" />
    </div>
  );
}

// Left Sidebar - narrow navigation column with icon-only navigation
// Requirements: 2.1, 2.2, 2.3, 2.4, 2.6
function LeftSidebar() {
  const { activeModal, openModal } = useLayout();

  // Modal-based navigation items
  const navItems: { icon: string; label: string; modalType: ModalType }[] = [
    { icon: 'workspaces', label: 'Workspaces', modalType: 'workspaces' },
    { icon: 'grid_view', label: 'SwarmCore', modalType: 'swarmcore' },
    { icon: 'smart_toy', label: 'Agents', modalType: 'agents' },
    { icon: 'auto_awesome', label: 'Skills', modalType: 'skills' },
    { icon: 'hub', label: 'MCP Servers', modalType: 'mcp' },
  ];

  return (
    <aside
      className="bg-[var(--color-bg-chrome)] border-r border-[var(--color-border)] flex flex-col flex-shrink-0"
      style={{ width: LEFT_SIDEBAR_WIDTH }}
      data-testid="left-sidebar"
    >
      {/* Logo/Brand area */}
      <div className="h-10 flex items-center justify-center border-b border-[var(--color-border)]">
        <SwarmAILogo />
      </div>

      {/* Navigation icons */}
      <nav className="flex-1 py-1.5 px-1 space-y-0.5 overflow-y-auto" data-testid="nav-icons">
        {navItems.map((item) => (
          <NavIconButton
            key={item.modalType}
            icon={item.icon}
            label={item.label}
            isActive={activeModal === item.modalType}
            onClick={() => openModal(item.modalType)}
            data-testid={`nav-${item.modalType}`}
          />
        ))}
      </nav>

      {/* Bottom section - Settings and GitHub link */}
      <div className="py-1.5 px-1 border-t border-[var(--color-border)] space-y-0.5">
        <NavIconButton
          icon="settings"
          label="Settings"
          isActive={activeModal === 'settings'}
          onClick={() => openModal('settings')}
          data-testid="nav-settings"
        />
        <a
          href="https://github.com/xg-gh-25/SwarmAI.git"
          target="_blank"
          rel="noopener noreferrer"
          title="GitHub"
          className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
          data-testid="github-link"
        >
          <GitHubIcon className="w-4 h-4" />
        </a>
      </div>
    </aside>
  );
}

// SwarmAI Logo component
function SwarmAILogo() {
  return (
    <div
      className="w-[26px] h-[26px] rounded-md flex items-center justify-center overflow-hidden"
      title="SwarmAI"
      data-testid="swarm-logo"
    >
      <img src="/swarmai-icon-3.png" alt="SwarmAI" className="w-full h-full object-contain" />
    </div>
  );
}

// Navigation icon button component
interface NavIconButtonProps {
  icon: string;
  label: string;
  isActive?: boolean;
  onClick?: () => void;
  'data-testid'?: string;
}

function NavIconButton({ icon, label, isActive, onClick, 'data-testid': testId }: NavIconButtonProps) {
  return (
    <button
      onClick={onClick}
      title={label}
      data-testid={testId}
      aria-pressed={isActive}
      className={`flex items-center justify-center w-8 h-8 rounded-lg transition-colors ${
        isActive
          ? 'bg-[var(--color-primary)]/15 text-[var(--color-sidebar-icon-active)] ring-1 ring-[var(--color-primary)]/30'
          : 'text-[var(--color-sidebar-icon)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      }`}
    >
      <span className="material-symbols-outlined text-[18px]">{icon}</span>
    </button>
  );
}

// GitHub SVG icon component
function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z" />
    </svg>
  );
}

// Main Chat Panel with drop zone and context bar
interface MainChatPanelProps {
  children: ReactNode;
}

function MainChatPanel({ children }: MainChatPanelProps) {
  return (
    <main
      className="flex-1 overflow-hidden bg-[var(--color-bg)] flex flex-col"
      style={{ minWidth: MIN_MAIN_CHAT_PANEL_WIDTH }}
    >
      {children}
    </main>
  );
}

/** Invisible bridge that captures refreshTree from ExplorerContext into a ref
 *  so that code outside the provider (e.g. FileEditorModal save handler) can
 *  trigger a tree refresh. */
function RefreshTreeBridge({ refreshTreeRef }: { refreshTreeRef: React.MutableRefObject<(() => void) | null> }) {
  const { refreshTree } = useTreeData();
  refreshTreeRef.current = refreshTree;
  return null;
}

// Inner layout component that uses the context
function ThreeColumnLayoutInner({ children }: ThreeColumnLayoutProps) {
  const { activeModal, closeModal, workspaceSettingsId } = useLayout();

  /** Ref to hold the ExplorerContext refreshTree function (set by bridge component inside provider). */
  const refreshTreeRef = useRef<(() => void) | null>(null);

  // File editor state - Requirement 9.1
  const [fileEditorState, setFileEditorState] = useState<{
    isOpen: boolean;
    filePath: string;
    fileName: string;
    workspaceId: string;
    content: string;
    isSwarmWorkspace: boolean;
    gitStatus?: GitStatus;
    readonly?: boolean;
    committedContent?: string;
  } | null>(null);

  // Binary preview state for images, PDFs, and unsupported files
  const [binaryPreviewState, setBinaryPreviewState] = useState<{
    isOpen: boolean;
    fileName: string;
    filePath: string;
    mode: 'image' | 'pdf' | 'unsupported';
  } | null>(null);

  // Swarm workspace warning state - Requirement 4.3
  const [swarmWarning, setSwarmWarning] = useState<{
    isOpen: boolean;
    pendingFile: FileTreeItem | null;
  }>({ isOpen: false, pendingFile: null });

  // Open file editor with content -- reads via backend API (no Tauri fs scope issues)
  const openFileEditor = useCallback(async (file: FileTreeItem, gitStatus?: GitStatus) => {
    try {
      const response = await api.get<{ content: string; path: string; name: string; readonly?: boolean }>(
        '/workspace/file',
        { params: { path: file.path } },
      );

      // Fetch committed version for diff when file has git changes
      let committedContent: string | undefined;
      if (gitStatus) {
        try {
          const committedResponse = await api.get<{ content: string }>(
            '/workspace/file/committed',
            { params: { path: file.path } },
          );
          committedContent = committedResponse.data.content;
        } catch (err) {
          // Untracked or binary file -- fall back to empty string
          console.warn('Failed to fetch committed version:', err);
          committedContent = '';
        }
      }

      setFileEditorState({
        isOpen: true,
        filePath: file.path,
        fileName: file.name,
        workspaceId: file.workspaceId,
        content: response.data.content,
        isSwarmWorkspace: file.isSwarmWorkspace || false,
        gitStatus,
        readonly: response.data.readonly ?? false,
        committedContent,
      });
    } catch (error) {
      console.error('Failed to read file:', error);
    }
  }, []);

  // Handle file double-click - Requirement 9.1, 1.1-1.5, 7.1-7.2
  const handleFileDoubleClick = useCallback(async (file: FileTreeItem) => {
    if (file.isSwarmWorkspace) {
      setSwarmWarning({ isOpen: true, pendingFile: file });
      return;
    }

    const previewType: FilePreviewType = classifyFileForPreview(file.name);

    if (previewType === 'text') {
      await openFileEditor(file, file.gitStatus);
    } else {
      setBinaryPreviewState({
        isOpen: true,
        fileName: file.name,
        filePath: file.path,
        mode: previewType,
      });
    }
  }, [openFileEditor]);

  // Handle Swarm workspace warning confirmation
  const handleSwarmWarningConfirm = useCallback(async () => {
    if (swarmWarning.pendingFile) {
      await openFileEditor(swarmWarning.pendingFile, swarmWarning.pendingFile.gitStatus);
    }
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, [swarmWarning.pendingFile, openFileEditor]);

  const handleSwarmWarningCancel = useCallback(() => {
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, []);

  // Handle file save - Requirement 9.6
  const handleFileSave = useCallback(async (content: string) => {
    if (!fileEditorState) return;

    try {
      await api.put('/workspace/file', { content }, {
        params: { path: fileEditorState.filePath },
      });
      refreshTreeRef.current?.();
    } catch (error) {
      console.error('Failed to save file:', error);
      throw error;
    }
  }, [fileEditorState]);

  // Handle file editor close - Requirement 9.7
  const handleFileEditorClose = useCallback(() => {
    setFileEditorState(null);
    refreshTreeRef.current?.();
  }, []);

  return (
    <div className="flex flex-col h-screen bg-[var(--color-bg)]">
      <ExplorerProvider>
        <RefreshTreeBridge refreshTreeRef={refreshTreeRef} />

        {/* Top bar -- session context, draggable */}
        <TopBar />

        {/* Main layout below top bar */}
        <div className="flex flex-1 overflow-hidden">
          <LeftSidebar />
          <WorkspaceExplorer onFileDoubleClick={handleFileDoubleClick} />
          <MainChatPanel>{children}</MainChatPanel>
        </div>

        {/* Bottom status bar */}
        <BottomBar />
      </ExplorerProvider>

      {/* Binary Preview Modal */}
      {binaryPreviewState && (
        <BinaryPreviewModal
          isOpen={binaryPreviewState.isOpen}
          fileName={binaryPreviewState.fileName}
          filePath={binaryPreviewState.filePath}
          mode={binaryPreviewState.mode}
          onClose={() => setBinaryPreviewState(null)}
        />
      )}

      {/* File Editor Modal */}
      {fileEditorState && (
        <FileEditorModal
          isOpen={fileEditorState.isOpen}
          filePath={fileEditorState.filePath}
          fileName={fileEditorState.fileName}
          workspaceId={fileEditorState.workspaceId}
          initialContent={fileEditorState.content}
          onSave={handleFileSave}
          onClose={handleFileEditorClose}
          gitStatus={fileEditorState.gitStatus}
          readonly={fileEditorState.readonly}
          committedContent={fileEditorState.committedContent}
        />
      )}

      {/* Swarm Workspace Warning Dialog */}
      <SwarmWorkspaceWarningDialog
        isOpen={swarmWarning.isOpen}
        action="edit"
        fileName={swarmWarning.pendingFile?.name}
        onConfirm={handleSwarmWarningConfirm}
        onCancel={handleSwarmWarningCancel}
      />

      {/* Management Page Modals */}
      <WorkspacesModal isOpen={activeModal === 'workspaces'} onClose={closeModal} />
      <SwarmCoreModal isOpen={activeModal === 'swarmcore'} onClose={closeModal} />
      <SkillsModal isOpen={activeModal === 'skills'} onClose={closeModal} />
      <MCPSettingsModal isOpen={activeModal === 'mcp'} onClose={closeModal} />
      <AgentsModal isOpen={activeModal === 'agents'} onClose={closeModal} />
      <SettingsModal isOpen={activeModal === 'settings'} onClose={closeModal} />
      <WorkspaceSettingsModal
        isOpen={activeModal === 'workspace-settings'}
        onClose={closeModal}
        workspaceId={workspaceSettingsId}
      />
    </div>
  );
}

// Main component that wraps with LayoutProvider
export default function ThreeColumnLayout({ children }: ThreeColumnLayoutProps) {
  return (
    <LayoutProvider>
      <ThreeColumnLayoutInner>{children}</ThreeColumnLayoutInner>
    </LayoutProvider>
  );
}

// Export sub-components for potential reuse
export { TopBar, LeftSidebar, WorkspaceExplorer, MainChatPanel, NavIconButton, GitHubIcon, SwarmAILogo };
export { LEFT_SIDEBAR_WIDTH, MIN_MAIN_CHAT_PANEL_WIDTH };
