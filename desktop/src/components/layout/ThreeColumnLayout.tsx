import { ReactNode, useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { useQuery } from '@tanstack/react-query';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { ExplorerProvider, useTreeData } from '../../contexts/ExplorerContext';
import { WorkspaceExplorer } from '../workspace-explorer';
import { BottomBar } from './BottomBar';
import FileEditorModal from '../common/FileEditorModal';
import FileViewerPanel from '../file-viewer/FileViewerPanel';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';
import { openExternal } from '../../utils/openExternal';
import SettingsModal from '../modals/SettingsModal';
import WorkspaceSettingsModal from '../modals/WorkspaceSettingsModal';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import type { GitStatus } from '../../types';
import api from '../../services/api';
import { useToast } from '../../contexts/ToastContext';


// Left sidebar width constant
const LEFT_SIDEBAR_WIDTH = LAYOUT_CONSTANTS.LEFT_SIDEBAR_WIDTH;

// Minimum width for main chat panel to ensure usability
const MIN_MAIN_CHAT_PANEL_WIDTH = 300;

interface ThreeColumnLayoutProps {
  children: ReactNode;
}

// TopBar -- App-level intelligence bar.
// Shows: context ring (left), token usage metrics (right).
// Remains draggable for Tauri window move (macOS).
interface TokenUsageData {
  today_tokens_m: number;
  total_tokens_m: number;
  today_cost_usd: number;
  total_cost_usd: number;
}

function formatTokens(m: number): string {
  if (m >= 1000) return `${(m / 1000).toFixed(1)}B`;
  if (m >= 1) return `${m.toFixed(1)}M`;
  if (m >= 0.01) return `${(m * 1000).toFixed(0)}K`;
  return '0';
}

function TopBar() {
  const { data: tokenUsage } = useQuery<TokenUsageData>({
    queryKey: ['token-usage'],
    queryFn: async () => {
      const resp = await api.get<TokenUsageData>('/system/tokens/usage');
      return resp.data;
    },
    refetchInterval: 30_000, // refresh every 30s
    staleTime: 10_000,
  });

  const handleMouseDown = async (e: React.MouseEvent) => {
    if (e.button === 0 && e.clientX > 80) {
      try {
        await getCurrentWindow().startDragging();
      } catch (err) {
        console.error('Failed to start dragging:', err);
      }
    }
  };

  const todayDisplay = tokenUsage ? formatTokens(tokenUsage.today_tokens_m) : '--';
  const totalDisplay = tokenUsage ? formatTokens(tokenUsage.total_tokens_m) : '--';

  return (
    <div
      onMouseDown={handleMouseDown}
      className="h-10 bg-[var(--color-bg-chrome)] border-b border-[var(--color-border)] flex-shrink-0 select-none cursor-default flex items-center"
      data-tauri-drag-region
      data-testid="top-bar"
    >
      {/* Spacer for macOS traffic lights */}
      <div className="w-20 flex-shrink-0" />

      {/* Center: drag region (flexible spacer) */}
      <div className="flex-1" />

      {/* Right: token usage metrics */}
      <div
        className="flex items-center gap-2 mr-8 text-[11px] text-[var(--color-text-muted)]"
        role="status"
        aria-label="Token usage"
        title={tokenUsage
          ? `Today: $${tokenUsage.today_cost_usd.toFixed(2)} | Total: $${tokenUsage.total_cost_usd.toFixed(2)}`
          : 'Loading...'}
      >
        <span className="text-[13px]">&#x1FA99;</span>
        <span>Today <strong className="text-[var(--color-text-secondary)]">{todayDisplay}</strong></span>
        <span className="text-[var(--color-border)]">|</span>
        <span>Total <strong className="text-[var(--color-text-secondary)]">{totalDisplay}</strong></span>
      </div>
    </div>
  );
}

// Left Sidebar - narrow navigation column with icon-only navigation
function LeftSidebar() {
  const { activeModal, openModal, settingsTab, setSettingsTab, workspaceExplorerCollapsed, setWorkspaceExplorerCollapsed } = useLayout();

  // Skills and MCP now open Settings with the corresponding tab pre-selected
  const handleNavClick = (target: 'skills' | 'mcp') => {
    const tabMap = { skills: 'skills', mcp: 'mcp-servers' };
    setSettingsTab(tabMap[target]);
    openModal('settings');
  };

  // Nav items with SVG icon identifiers (AC6: no emoji icons)
  const navItems: { icon: string; label: string; target: 'skills' | 'mcp' }[] = [
    { icon: 'lightning', label: 'Skills', target: 'skills' },
    { icon: 'server', label: 'MCP Servers', target: 'mcp' },
  ];

  return (
    <aside
      className="bg-[var(--color-bg-chrome)] border-r border-[var(--color-border)] flex flex-col flex-shrink-0"
      style={{ width: LEFT_SIDEBAR_WIDTH }}
      data-testid="left-sidebar"
    >
      {/* Logo/Brand area — click toggles workspace explorer */}
      <button
        className="h-10 flex items-center justify-center border-b border-[var(--color-border)] w-full hover:bg-[var(--color-hover)] transition-colors"
        onClick={() => setWorkspaceExplorerCollapsed(!workspaceExplorerCollapsed)}
        title={workspaceExplorerCollapsed ? 'Show workspace explorer' : 'Hide workspace explorer'}
        aria-label="Toggle workspace explorer"
        data-testid="logo-toggle"
      >
        <SwarmAILogo />
      </button>

      {/* Navigation icons */}
      <nav className="flex-1 pt-2 pb-1 space-y-1 overflow-y-auto flex flex-col items-center" data-testid="nav-icons">
        {navItems.map((item) => (
          <NavIconButton
            key={item.target}
            icon={item.icon}
            label={item.label}
            isActive={activeModal === 'settings' && settingsTab === (item.target === 'mcp' ? 'mcp-servers' : item.target)}
            onClick={() => handleNavClick(item.target)}
            data-testid={`nav-${item.target}`}
          />
        ))}
      </nav>

      {/* Bottom section - Settings and GitHub */}
      <div className="pt-1.5 pb-2 border-t border-[var(--color-border)] space-y-1 flex flex-col items-center">
        <NavIconButton
          icon="gear"
          label="Settings"
          isActive={activeModal === 'settings' && !settingsTab}
          onClick={() => { setSettingsTab(undefined); openModal('settings'); }}
          data-testid="nav-settings"
        />
        <a
          href="https://github.com/xg-gh-25/SwarmAI.git"
          title="GitHub"
          className="flex items-center justify-center w-8 h-8 rounded-lg transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] cursor-pointer"
          data-testid="github-link"
          onClick={(e) => {
            e.preventDefault();
            openExternal('https://github.com/xg-gh-25/SwarmAI.git');
          }}
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
      <img src="/swarm-avatar.svg" alt="SwarmAI" className="w-full h-full object-contain" />
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

/** SVG stroke icon lookup — AC6: replace Material Symbols with inline SVGs. */
function NavSvgIcon({ name }: { name: string }) {
  const svgProps = {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  };

  switch (name) {
    case 'lightning':
      return (
        <svg {...svgProps} aria-hidden="true">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
      );
    case 'server':
      return (
        <svg {...svgProps} aria-hidden="true">
          <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
          <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
          <line x1="6" y1="6" x2="6.01" y2="6" />
          <line x1="6" y1="18" x2="6.01" y2="18" />
        </svg>
      );
    case 'tune':
    case 'gear':
      return (
        <svg {...svgProps} aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
    default:
      // Fallback to material-symbols for unknown icons
      return <span className="material-symbols-outlined text-[18px]">{name}</span>;
  }
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
      <NavSvgIcon name={icon} />
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
  const { activeModal, closeModal, workspaceSettingsId, settingsTab } = useLayout();
  const { addToast } = useToast();

  /** Ref to hold the ExplorerContext refreshTree function (set by bridge component inside provider). */
  const refreshTreeRef = useRef<(() => void) | null>(null);

  // File viewer state — unified for all file types (Requirement 9.1)
  // editorMode: 'panel' = side panel (default), 'modal' = fullscreen overlay (text files only)
  const [editorMode, setEditorMode] = useState<'panel' | 'modal'>('panel');
  const [fileViewerFile, setFileViewerFile] = useState<{
    filePath: string;
    fileName: string;
    gitStatus?: GitStatus;
    workspaceId?: string;
  } | null>(null);

  // Legacy file editor state — kept for modal mode (fullscreen text editing only)
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

  // Swarm workspace warning state - Requirement 4.3
  const [swarmWarning, setSwarmWarning] = useState<{
    isOpen: boolean;
    pendingFile: FileTreeItem | null;
  }>({ isOpen: false, pendingFile: null });

  // Listen for swarm:open-file custom events dispatched by clickable file paths
  // in chat messages (MarkdownRenderer). Uses a ref to avoid stale closure on
  // handleFileDoubleClick which depends on external state.

  // Notify RadarSidebar when file viewer panel is open/closed so it can auto-hide
  const isEditorPanelOpen = !!(fileViewerFile && editorMode === 'panel');
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-panel-state', {
      detail: { open: isEditorPanelOpen },
    }));
  }, [isEditorPanelOpen]);

  // Notify ChatPage which file is currently open so it can include in chat requests.
  // Memoize the detail to avoid dispatching redundant null→null events.
  const editorFileDetail = useMemo(
    () => fileViewerFile
      ? { filePath: fileViewerFile.filePath, fileName: fileViewerFile.fileName }
      : null,
    [fileViewerFile?.filePath, fileViewerFile?.fileName],
  );
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-file-changed', {
      detail: editorFileDetail,
    }));
  }, [editorFileDetail]);

  // Ref for file open routing — assigned after handleFileDoubleClick is defined below
  const handleFileDoubleClickRef = useRef<(file: FileTreeItem) => Promise<void>>(null!);


  // Handle file double-click — unified routing through FileViewer (Requirement 9.1)
  const handleFileDoubleClick = useCallback(async (file: FileTreeItem) => {
    if (file.isSwarmWorkspace) {
      setSwarmWarning({ isOpen: true, pendingFile: file });
      return;
    }

    // All file types route through the unified FileViewer panel.
    // FileViewer internally classifies and picks the right renderer.
    setFileViewerFile({
      filePath: file.path,
      fileName: file.name,
      gitStatus: file.gitStatus,
      workspaceId: file.workspaceId,
    });
    // Reset modal mode — FileViewer always starts in panel
    setEditorMode('panel');
  }, []);

  // Assign ref now that handleFileDoubleClick is defined
  handleFileDoubleClickRef.current = handleFileDoubleClick;

  // Listen for swarm:open-file events from clickable file paths in chat.
  // Paths from chat may be relative to source repos, not the workspace root.
  // We call /workspace/file/resolve first to find the actual workspace path.
  useEffect(() => {
    let mounted = true;

    const handleOpenFileEvent = async (e: Event) => {
      const { path: filePath } = (e as CustomEvent<{ path: string }>).detail ?? {};
      if (!filePath) return;

      let resolvedPath = filePath;
      try {
        // Resolve partial/codebase-relative paths to workspace-relative paths
        const resp = await api.get<{ resolved_path: string }>(
          '/workspace/file/resolve',
          { params: { path: filePath } },
        );
        if (!mounted) return;
        resolvedPath = resp.data.resolved_path;
      } catch (err: unknown) {
        if (!mounted) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 400) {
          // Path traversal or truly invalid — don't fall through
          addToast({ severity: 'warning', message: `Cannot open file: ${filePath}`, autoDismiss: true });
          return;
        }
        // 404 = not found in workspace, fall through to try the raw path.
        // Non-404 errors (network timeout, 500) are logged for debugging.
        if (status !== undefined && status !== 404) {
          console.warn('[swarm:open-file] resolve failed:', status, err);
        }
      }

      const fileName = resolvedPath.split('/').pop() || resolvedPath;
      const fileItem: FileTreeItem = {
        id: resolvedPath,
        name: fileName,
        type: 'file',
        path: resolvedPath,
        workspaceId: '',
        workspaceName: '',
      };

      // Route through handleFileDoubleClick for proper file type handling
      // (images preview inline, binary files show info modal, text opens editor).
      try {
        await handleFileDoubleClickRef.current(fileItem);
      } catch {
        if (!mounted) return;
        addToast({
          severity: 'warning',
          message: `Could not open file: ${filePath}`,
          autoDismiss: true,
        });
      }
    };

    document.addEventListener('swarm:open-file', handleOpenFileEvent);
    return () => {
      mounted = false;
      document.removeEventListener('swarm:open-file', handleOpenFileEvent);
    };
  }, [addToast]);

  // Handle Swarm workspace warning confirmation
  const handleSwarmWarningConfirm = useCallback(async () => {
    if (swarmWarning.pendingFile) {
      setFileViewerFile({
        filePath: swarmWarning.pendingFile.path,
        fileName: swarmWarning.pendingFile.name,
        gitStatus: swarmWarning.pendingFile.gitStatus,
        workspaceId: swarmWarning.pendingFile.workspaceId,
      });
    }
    setSwarmWarning({ isOpen: false, pendingFile: null });
  }, [swarmWarning.pendingFile]);

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

  // Handle file viewer close - Requirement 9.7
  const handleFileViewerClose = useCallback(() => {
    setFileViewerFile(null);
    setFileEditorState(null); // Clear legacy state too
    setEditorMode('panel'); // Reset to panel for next open
    liveContentRef.current = null;
    refreshTreeRef.current?.();
  }, []);

  // Track live content in a ref (NOT state) so mode toggle preserves edits
  // without triggering re-renders or resetting FileEditorCore's useEffect.
  const liveContentRef = useRef<string | null>(null);

  const handleContentChange = useCallback((newContent: string) => {
    liveContentRef.current = newContent;
  }, []);

  // Toggle between panel and modal mode (preserves file state).
  // Panel→modal: populate fileEditorState from fileViewerFile for FileEditorModal.
  // Modal→panel: clear fileEditorState, let FileViewer take over.
  const handleToggleEditorMode = useCallback(async () => {
    if (editorMode === 'panel' && fileViewerFile) {
      // Panel → Modal: need to populate legacy fileEditorState for FileEditorModal
      try {
        const response = await api.get<{ content: string; path: string; name: string; readonly?: boolean }>(
          '/workspace/file',
          { params: { path: fileViewerFile.filePath } },
        );
        let committedContent: string | undefined;
        try {
          const cResp = await api.get<{ content: string }>(
            '/workspace/file/committed',
            { params: { path: fileViewerFile.filePath } },
          );
          committedContent = cResp.data.content;
        } catch { /* untracked file */ }

        const content = liveContentRef.current ?? response.data.content;
        setFileEditorState({
          isOpen: true,
          filePath: fileViewerFile.filePath,
          fileName: fileViewerFile.fileName,
          workspaceId: fileViewerFile.workspaceId ?? '',
          content,
          isSwarmWorkspace: false,
          gitStatus: fileViewerFile.gitStatus,
          readonly: response.data.readonly,
          committedContent,
        });
      } catch (err) {
        console.error('Failed to switch to modal mode:', err);
        return;
      }
    } else if (editorMode === 'modal' && fileEditorState) {
      // Modal → Panel: snapshot live content, clear legacy state
      if (liveContentRef.current != null) {
        // Content preserved via liveContentRef — FileViewer will re-fetch
      }
      setFileEditorState(null);
    }
    liveContentRef.current = null;
    setEditorMode((prev) => (prev === 'panel' ? 'modal' : 'panel'));
  }, [editorMode, fileViewerFile, fileEditorState]);

  // L2: Auto-diff feedback — inject edit summary into chat input.
  // Accepts fileName as a parameter to avoid stale-closure reads of
  // fileEditorState (which may be nulled if the editor closes during
  // the async diff fetch).
  const handleSaveWithDiff = useCallback((diffSummary: string, savedFileName?: string) => {
    const fileName = savedFileName ?? fileEditorState?.fileName ?? 'file';
    const text = `I edited \`${fileName}\`:\n${diffSummary}\n\nPlease revise the doc to align with these changes.`;
    window.dispatchEvent(new CustomEvent('swarm:inject-chat-input', {
      detail: { text, focus: true },
    }));
  }, [fileEditorState?.fileName]);

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
          {/* Unified File Viewer — resizable side panel for all file types */}
          {fileViewerFile && editorMode === 'panel' && (
            <FileViewerPanel
              initialFile={fileViewerFile}
              onClose={handleFileViewerClose}
              onSaveWithDiff={handleSaveWithDiff}
              onToggleMode={handleToggleEditorMode}
            />
          )}
        </div>

        {/* Bottom status bar */}
        <BottomBar />
      </ExplorerProvider>

      {/* File Editor Modal — fullscreen overlay mode (text files only, via toggle) */}
      {fileEditorState && editorMode === 'modal' && (
        <FileEditorModal
          isOpen={fileEditorState.isOpen}
          filePath={fileEditorState.filePath}
          fileName={fileEditorState.fileName}
          workspaceId={fileEditorState.workspaceId}
          initialContent={fileEditorState.content}
          onSave={handleFileSave}
          onClose={handleFileViewerClose}
          gitStatus={fileEditorState.gitStatus}
          readonly={fileEditorState.readonly}
          committedContent={fileEditorState.committedContent}
          onToggleMode={handleToggleEditorMode}
          onSaveWithDiff={handleSaveWithDiff}
          onContentChange={handleContentChange}
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
      {/* Skills and MCP now integrated into Settings tabs — standalone modals removed */}
      <SettingsModal isOpen={activeModal === 'settings'} onClose={closeModal} initialTab={settingsTab} />
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
