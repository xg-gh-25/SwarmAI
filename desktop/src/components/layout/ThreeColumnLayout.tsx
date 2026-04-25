import { ReactNode, useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { useQuery } from '@tanstack/react-query';
import { LayoutProvider, useLayout, LAYOUT_CONSTANTS } from '../../contexts/LayoutContext';
import { ExplorerProvider, useTreeData } from '../../contexts/ExplorerContext';
import { WorkspaceExplorer } from '../workspace-explorer';
import { BottomBar } from './BottomBar';
import FileEditorModal from '../common/FileEditorModal';
import FileEditorPanel from '../common/FileEditorPanel';
import BinaryPreviewModal from '../common/BinaryPreviewModal';
import SwarmWorkspaceWarningDialog from '../common/SwarmWorkspaceWarningDialog';
import { classifyFileForPreview } from '../../utils/fileUtils';
import { openExternal } from '../../utils/openExternal';
import type { FilePreviewType } from '../../utils/fileUtils';
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

  // Legacy nav items kept for reference — now handled by handleNavClick
  const navItems: { icon: string; label: string; target: 'skills' | 'mcp' }[] = [
    { icon: 'extension', label: 'Skills', target: 'skills' },
    { icon: 'device_hub', label: 'MCP Servers', target: 'mcp' },
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
          icon="tune"
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
  const { activeModal, closeModal, workspaceSettingsId, settingsTab } = useLayout();
  const { addToast } = useToast();

  /** Ref to hold the ExplorerContext refreshTree function (set by bridge component inside provider). */
  const refreshTreeRef = useRef<(() => void) | null>(null);

  // File editor state - Requirement 9.1
  // editorMode: 'panel' = side panel (default), 'modal' = fullscreen overlay
  const [editorMode, setEditorMode] = useState<'panel' | 'modal'>('panel');
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
    mode: 'image' | 'unsupported';
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

      // Always fetch committed (HEAD) version for Show Changes diff.
      // Previously gated on gitStatus, but files opened via swarm:open-file
      // events (chat links) don't carry gitStatus, so committedContent was
      // never fetched for them.  The try/catch handles untracked/new files.
      let committedContent: string | undefined;
      try {
        const committedResponse = await api.get<{ content: string }>(
          '/workspace/file/committed',
          { params: { path: file.path } },
        );
        committedContent = committedResponse.data.content;
      } catch {
        // Untracked, new, or binary file — no committed version available.
        // Leave undefined so originalContent falls back to initialContent.
      }

      liveContentRef.current = null; // Reset live content tracking for new file
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
      addToast({
        severity: 'warning',
        message: `File not found: ${file.path}`,
        autoDismiss: true,
      });
    }
  }, [addToast]);

  // Listen for swarm:open-file custom events dispatched by clickable file paths
  // in chat messages (MarkdownRenderer). Uses a ref to avoid stale closure on
  // openFileEditor which depends on external state.

  // Notify RadarSidebar when file editor panel is open/closed so it can auto-hide
  const isEditorPanelOpen = !!(fileEditorState && editorMode === 'panel');
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-panel-state', {
      detail: { open: isEditorPanelOpen },
    }));
  }, [isEditorPanelOpen]);

  // Notify ChatPage which file is currently open so it can include in chat requests.
  // Memoize the detail to avoid dispatching redundant null→null events.
  const editorFileDetail = useMemo(
    () => fileEditorState
      ? { filePath: fileEditorState.filePath, fileName: fileEditorState.fileName }
      : null,
    [fileEditorState?.filePath, fileEditorState?.fileName],
  );
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('swarm:editor-file-changed', {
      detail: editorFileDetail,
    }));
  }, [editorFileDetail]);

  // Ref for file open routing — assigned after handleFileDoubleClick is defined below
  const handleFileDoubleClickRef = useRef<(file: FileTreeItem) => Promise<void>>(null!);


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
      // 'image' or 'unsupported' — show in BinaryPreviewModal
      // (images render inline; unsupported shows file info + Open/Copy Path)
      setBinaryPreviewState({
        isOpen: true,
        fileName: file.name,
        filePath: file.path,
        mode: previewType,
      });
    }
  }, [openFileEditor]);

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
  // Snapshot the live content into fileEditorState so the remounted
  // editor picks it up as initialContent.
  const handleToggleEditorMode = useCallback(() => {
    if (liveContentRef.current != null) {
      setFileEditorState((prev) =>
        prev ? { ...prev, content: liveContentRef.current! } : prev,
      );
    }
    setEditorMode((prev) => (prev === 'panel' ? 'modal' : 'panel'));
  }, []);

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
          {/* File Editor Panel — side-by-side with chat */}
          {fileEditorState && editorMode === 'panel' && (
            <FileEditorPanel
              filePath={fileEditorState.filePath}
              fileName={fileEditorState.fileName}
              workspaceId={fileEditorState.workspaceId}
              initialContent={fileEditorState.content}
              onSave={handleFileSave}
              onClose={handleFileEditorClose}
              gitStatus={fileEditorState.gitStatus}
              readonly={fileEditorState.readonly}
              committedContent={fileEditorState.committedContent}
              onToggleMode={handleToggleEditorMode}
              onSaveWithDiff={handleSaveWithDiff}
              onContentChange={handleContentChange}
            />
          )}
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

      {/* File Editor Modal — fullscreen overlay mode */}
      {fileEditorState && editorMode === 'modal' && (
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
