import { ReactNode, useState, useCallback } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS, ModalType } from '../../contexts/LayoutContext';
import { ExplorerProvider } from '../../contexts/ExplorerContext';
import { WorkspaceExplorer } from '../workspace-explorer';
import { ChatDropZone } from '../chat';
import GlobalSearchBar from './GlobalSearchBar';
import FileEditorModal from '../common/FileEditorModal';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';
import SkillsModal from '../modals/SkillsModal';
import MCPServersModal from '../modals/MCPServersModal';
import AgentsModal from '../modals/AgentsModal';
import SettingsModal from '../modals/SettingsModal';
import WorkspacesModal from '../modals/WorkspacesModal';
import SwarmCoreModal from '../modals/SwarmCoreModal';
import WorkspaceSettingsModal from '../modals/WorkspaceSettingsModal';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import api from '../../services/api';

// Left sidebar width constant
const LEFT_SIDEBAR_WIDTH = LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH;

// Minimum width for main chat panel to ensure usability
const MIN_MAIN_CHAT_PANEL_WIDTH = 300;

interface ThreeColumnLayoutProps {
  children: ReactNode;
}

// TopBar component with window dragging support and centered GlobalSearchBar.
// The bar remains draggable (Tauri window drag) except over the search input,
// which stops mouseDown propagation to prevent window dragging.
// Requirements: 9.2, 13.1
function TopBar() {
  const handleMouseDown = async (e: React.MouseEvent) => {
    // Only start drag on left mouse button and not on traffic light area (macOS)
    if (e.button === 0 && e.clientX > 80) {
      try {
        await getCurrentWindow().startDragging();
      } catch (err) {
        console.error('Failed to start dragging:', err);
      }
    }
  };

  return (
    <div
      onMouseDown={handleMouseDown}
      className="h-10 bg-[var(--color-bg)] border-b border-[var(--color-border)] flex-shrink-0 select-none cursor-default flex items-center"
      data-tauri-drag-region
    >
      {/* Spacer for macOS traffic lights */}
      <div className="w-20 flex-shrink-0" />
      {/* GlobalSearchBar — centered, stops drag propagation internally */}
      <GlobalSearchBar />
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
      className="bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col flex-shrink-0"
      style={{ width: LEFT_SIDEBAR_WIDTH }}
      data-testid="left-sidebar"
    >
      {/* Logo/Brand area - Requirement 2.4 */}
      <div className="h-12 flex items-center justify-center border-b border-[var(--color-border)]">
        <SwarmAILogo />
      </div>

      {/* Navigation icons - Requirement 2.1, 2.2 */}
      <nav className="flex-1 py-3 px-2 space-y-1 overflow-y-auto" data-testid="nav-icons">
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
      <div className="py-3 px-2 border-t border-[var(--color-border)] space-y-1">
        {/* Settings - Requirement 2.1, 2.2 */}
        <NavIconButton
          icon="settings"
          label="Settings"
          isActive={activeModal === 'settings'}
          onClick={() => openModal('settings')}
          data-testid="nav-settings"
        />
        {/* GitHub Link - Requirement 2.6 */}
        <a
          href="https://github.com/xg-gh-25/SwarmAI.git"
          target="_blank"
          rel="noopener noreferrer"
          title="GitHub"
          className="flex items-center justify-center w-10 h-10 rounded-lg transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
          data-testid="github-link"
        >
          <GitHubIcon className="w-5 h-5" />
        </a>
      </div>
    </aside>
  );
}

// SwarmAI Logo component - Requirement 2.4
function SwarmAILogo() {
  return (
    <div 
      className="w-8 h-8 rounded-lg flex items-center justify-center overflow-hidden"
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
      className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors ${
        isActive
          ? 'bg-[var(--color-primary)]/15 text-[var(--color-sidebar-icon-active)] ring-1 ring-[var(--color-primary)]/30'
          : 'text-[var(--color-sidebar-icon)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
      }`}
    >
      <span className="material-symbols-outlined text-xl">{icon}</span>
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
      {/* Drop zone wrapper for drag-drop file attachment */}
      <ChatDropZone>
        {children}
      </ChatDropZone>
    </main>
  );
}

// Inner layout component that uses the context
function ThreeColumnLayoutInner({ children }: ThreeColumnLayoutProps) {
  const { activeModal, closeModal, workspaceSettingsId, attachFile } = useLayout();

  // File editor state - Requirement 9.1
  const [fileEditorState, setFileEditorState] = useState<{
    isOpen: boolean;
    filePath: string;
    fileName: string;
    workspaceId: string;
    content: string;
    isSwarmWorkspace: boolean;
  } | null>(null);

  // Swarm workspace warning state - Requirement 4.3
  const [swarmWarning, setSwarmWarning] = useState<{
    isOpen: boolean;
    pendingFile: FileTreeItem | null;
  }>({ isOpen: false, pendingFile: null });

  // Open file editor with content — reads via backend API (no Tauri fs scope issues)
  const openFileEditor = useCallback(async (file: FileTreeItem) => {
    try {
      const response = await api.get<{ content: string; path: string; name: string }>(
        '/workspace/file',
        { params: { path: file.path } },
      );

      setFileEditorState({
        isOpen: true,
        filePath: file.path,
        fileName: file.name,
        workspaceId: file.workspaceId,
        content: response.data.content,
        isSwarmWorkspace: file.isSwarmWorkspace || false,
      });
    } catch (error) {
      console.error('Failed to read file:', error);
      // TODO: Show error toast
    }
  }, []);

  // Handle file double-click - Requirement 9.1
  const handleFileDoubleClick = useCallback(async (file: FileTreeItem) => {
    // Check if this is a Swarm Workspace file - Requirement 4.3
    if (file.isSwarmWorkspace) {
      setSwarmWarning({ isOpen: true, pendingFile: file });
      return;
    }

    // Open the file editor
    await openFileEditor(file);
  }, [openFileEditor]);

  // Handle Swarm workspace warning confirmation - Requirement 4.3, 4.5
  const handleSwarmWarningConfirm = useCallback(async () => {
    if (swarmWarning.pendingFile) {
      await openFileEditor(swarmWarning.pendingFile);
    }
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, [swarmWarning.pendingFile, openFileEditor]);

  // Handle Swarm workspace warning cancel
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
    } catch (error) {
      console.error('Failed to save file:', error);
      throw error; // Re-throw to keep modal open
    }
  }, [fileEditorState]);

  // Handle file editor close - Requirement 9.7
  const handleFileEditorClose = useCallback(() => {
    setFileEditorState(null);
  }, []);

  return (
    <div className="flex flex-col h-screen bg-[var(--color-bg)]">
      <ExplorerProvider>
        {/* Top bar with traffic lights area - draggable */}
        <TopBar />

        {/* Main layout below top bar */}
        <div className="flex flex-1 overflow-hidden">
          {/* Left Sidebar - 56px fixed width */}
          <LeftSidebar />

          {/* Workspace Explorer - 280px default, resizable 200-500px, collapsible */}
          <WorkspaceExplorer onFileDoubleClick={handleFileDoubleClick} onAttachToChat={attachFile} />

          {/* Main Chat Panel - flex-1 (remaining space) */}
          <MainChatPanel>{children}</MainChatPanel>
        </div>
      </ExplorerProvider>

      {/* File Editor Modal - Requirement 9.1, 9.2 */}
      {fileEditorState && (
        <FileEditorModal
          isOpen={fileEditorState.isOpen}
          filePath={fileEditorState.filePath}
          fileName={fileEditorState.fileName}
          workspaceId={fileEditorState.workspaceId}
          initialContent={fileEditorState.content}
          onSave={handleFileSave}
          onClose={handleFileEditorClose}
        />
      )}

      {/* Swarm Workspace Warning Dialog - Requirement 4.3, 4.5 */}
      <SwarmWorkspaceWarningDialog
        isOpen={swarmWarning.isOpen}
        action="edit"
        fileName={swarmWarning.pendingFile?.name}
        onConfirm={handleSwarmWarningConfirm}
        onCancel={handleSwarmWarningCancel}
      />

      {/* Management Page Modals - Requirement 2.2 */}
      <WorkspacesModal isOpen={activeModal === 'workspaces'} onClose={closeModal} />
      <SwarmCoreModal isOpen={activeModal === 'swarmcore'} onClose={closeModal} />
      <SkillsModal isOpen={activeModal === 'skills'} onClose={closeModal} />
      <MCPServersModal isOpen={activeModal === 'mcp'} onClose={closeModal} />
      <AgentsModal isOpen={activeModal === 'agents'} onClose={closeModal} />
      <SettingsModal isOpen={activeModal === 'settings'} onClose={closeModal} />
      {/* Workspace Settings Modal - Requirement 3.14 */}
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
