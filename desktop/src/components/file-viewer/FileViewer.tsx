/**
 * FileViewer — Unified file viewer orchestrator for SwarmAI.
 *
 * Replaces the old FileEditorPanel + BinaryPreviewModal split with a single
 * tabbed surface that routes to the correct renderer based on file type.
 *
 * Architecture:
 *   FileViewer
 *     FileViewerTabBar        — horizontal tab strip (top)
 *     Renderer Area           — flex-1 middle area
 *       text/markdown/svg     -> FileEditorCore (existing, strangler-fig wrap)
 *       image/pdf/html/...    -> lazy-loaded per-type renderers
 *       unsupported           -> UnsupportedRenderer
 *     FileViewerStatusBar     — slim info bar (bottom)
 *
 * The text/markdown/svg path delegates to the *existing* FileEditorCore —
 * no editor logic is duplicated here. FileViewer just wraps it with tabs
 * and a status bar.
 */

import {
  useState,
  useEffect,
  useCallback,
  useRef,
  lazy,
  Suspense,
} from 'react';
import type { GitStatus } from '../../types';
import type { FileTreeItem } from '../workspace-explorer/FileTreeNode';
import { isEditableType, isBinaryType } from './utils/fileViewTypes';
import { useFileViewerTabs } from './hooks/useFileViewerTabs';
import type { FileTab } from './hooks/useFileViewerTabs';
import FileViewerTabBar from './FileViewerTabBar';
import FileViewerStatusBar from './FileViewerStatusBar';
import FileEditorCore from '../common/FileEditorCore';
import api from '../../services/api';

/* ------------------------------------------------------------------ */
/*  Lazy-loaded renderers (code-split per type)                        */
/* ------------------------------------------------------------------ */

const ImageRenderer = lazy(() => import('./renderers/ImageRenderer'));
const PdfRenderer = lazy(() => import('./renderers/PdfRenderer'));
const HtmlRenderer = lazy(() => import('./renderers/HtmlRenderer'));
const VideoRenderer = lazy(() => import('./renderers/VideoRenderer'));
const AudioRenderer = lazy(() => import('./renderers/AudioRenderer'));
const CsvRenderer = lazy(() => import('./renderers/CsvRenderer'));
const UnsupportedRenderer = lazy(() => import('./renderers/UnsupportedRenderer'));

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface FileViewerProps {
  /** Initial file to open (e.g. from Explorer double-click). */
  initialFile?: {
    filePath: string;
    fileName: string;
    gitStatus?: GitStatus;
    workspaceId?: string;
  };
  onClose: () => void;
  onAttachToChat?: (item: FileTreeItem) => void;
  onSaveWithDiff?: (diffSummary: string, fileName?: string) => void;
  /** Whether this viewer is mounted as a resizable side panel or fullscreen overlay. */
  variant: 'panel' | 'modal';
  /** Toggle between panel and modal mode. */
  onToggleMode?: () => void;
}

/** Status-bar info that renderers can publish via onStatusInfo callback. */
export interface StatusBarInfo {
  fileSize: number;
  encoding?: string;
  extraInfo?: Record<string, string>;
}

/** Response shape from GET /workspace/file. */
interface FileResponse {
  content: string;
  encoding?: string;
  mime_type?: string;
  mimeType?: string;
  size?: number;
  name: string;
  path: string;
  readonly?: boolean;
}

/* ------------------------------------------------------------------ */
/*  Loading spinner                                                    */
/* ------------------------------------------------------------------ */

function LoadingFallback() {
  return (
    <div className="flex-1 flex items-center justify-center text-[var(--color-text-secondary)]">
      <span className="material-symbols-outlined animate-spin text-2xl mr-2">progress_activity</span>
      <span className="text-sm">Loading...</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  FileViewer                                                         */
/* ------------------------------------------------------------------ */

export default function FileViewer({
  initialFile,
  onClose,
  onAttachToChat,
  onSaveWithDiff,
  variant,
  onToggleMode,
}: FileViewerProps) {
  const {
    tabs,
    activeTab,
    openTab,
    closeTab,
    switchTab,
    markDirty,
  } = useFileViewerTabs();

  /* ---- Per-tab content cache ---- */
  // Keeps fetched content keyed by filePath so we don't re-fetch on tab switch.
  const contentCache = useRef<
    Record<
      string,
      {
        content: string;
        encoding?: string;
        size: number;
        mimeType?: string;
        readonly?: boolean;
        committedContent?: string;
      }
    >
  >({}); // React 19 requires initial value

  /* ---- Status bar info (updated by renderers) ---- */
  const [statusBarInfo, setStatusBarInfo] = useState<StatusBarInfo>({
    fileSize: 0,
  });

  /* ---- Content loading state for the active tab ---- */
  const [loadingContent, setLoadingContent] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);

  /* ---- workspaceId placeholder (FileEditorCore needs it) ---- */
  // The unified viewer does not depend on workspaceId for routing, but
  // FileEditorCore requires it for attach-to-chat. We pass '' as a
  // default; the parent can enrich initialFile if needed.
  const workspaceIdRef = useRef<string>('');

  /* -------------------------------------------------------------- */
  /*  Open initial file when prop changes                            */
  /* -------------------------------------------------------------- */

  const prevInitialFileRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!initialFile) return;
    if (prevInitialFileRef.current === initialFile.filePath) return;
    prevInitialFileRef.current = initialFile.filePath;
    // Capture workspaceId for FileEditorCore's attach-to-chat
    if (initialFile.workspaceId) {
      workspaceIdRef.current = initialFile.workspaceId;
    }
    openTab(initialFile.filePath, initialFile.fileName, initialFile.gitStatus);
  }, [initialFile, openTab]);

  /* -------------------------------------------------------------- */
  /*  Fetch content for active tab                                   */
  /* -------------------------------------------------------------- */

  useEffect(() => {
    if (!activeTab) return;

    // Already cached
    if (contentCache.current[activeTab.filePath]) {
      const cached = contentCache.current[activeTab.filePath];
      setStatusBarInfo({
        fileSize: cached.size,
        encoding: cached.encoding,
      });
      setContentError(null);
      return;
    }

    let cancelled = false;

    async function fetchFileContent(tab: FileTab) {
      setLoadingContent(true);
      setContentError(null);

      try {
        if (isBinaryType(tab.viewType) && tab.viewType !== 'unsupported') {
          // Binary content (image, pdf, video, audio) — full fetch
          const resp = await api.get<FileResponse>('/workspace/file', {
            params: { path: tab.filePath },
          });
          if (cancelled) return;
          const d = resp.data;
          contentCache.current[tab.filePath] = {
            content: d.content,
            encoding: d.encoding,
            size: d.size ?? 0,
            mimeType: d.mime_type ?? d.mimeType,
          };
          setStatusBarInfo({ fileSize: d.size ?? 0, encoding: d.encoding });
        } else if (tab.viewType === 'unsupported') {
          // Unsupported — metadata only
          try {
            const resp = await api.get<{ size: number; mime_type: string }>(
              '/workspace/file/meta',
              { params: { path: tab.filePath } },
            );
            if (cancelled) return;
            contentCache.current[tab.filePath] = {
              content: '',
              size: resp.data.size ?? 0,
              mimeType: resp.data.mime_type,
            };
            setStatusBarInfo({ fileSize: resp.data.size ?? 0 });
          } catch {
            // Metadata fetch is best-effort for unsupported files
            if (cancelled) return;
            contentCache.current[tab.filePath] = { content: '', size: 0 };
            setStatusBarInfo({ fileSize: 0 });
          }
        } else {
          // Text / markdown / svg / html / csv — text fetch
          const resp = await api.get<FileResponse>('/workspace/file', {
            params: { path: tab.filePath },
          });
          if (cancelled) return;

          const d = resp.data;
          let committedContent: string | undefined;

          // Fetch committed (HEAD) version for diff in editable types
          if (isEditableType(tab.viewType)) {
            try {
              const cResp = await api.get<{ content: string }>(
                '/workspace/file/committed',
                { params: { path: tab.filePath } },
              );
              if (!cancelled) committedContent = cResp.data.content;
            } catch {
              // New/untracked file — no committed version
            }
          }

          if (cancelled) return;
          contentCache.current[tab.filePath] = {
            content: d.content,
            encoding: d.encoding ?? 'utf-8',
            size: d.size ?? new TextEncoder().encode(d.content).length,
            readonly: d.readonly,
            committedContent,
          };
          setStatusBarInfo({
            fileSize: d.size ?? new TextEncoder().encode(d.content).length,
            encoding: d.encoding ?? 'utf-8',
          });
        }
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : 'Failed to load file';
        setContentError(msg);
      } finally {
        if (!cancelled) setLoadingContent(false);
      }
    }

    fetchFileContent(activeTab);
    return () => {
      cancelled = true;
    };
  }, [activeTab?.filePath, activeTab?.viewType]); // eslint-disable-line react-hooks/exhaustive-deps

  /* -------------------------------------------------------------- */
  /*  Save handler for text-editable files                           */
  /* -------------------------------------------------------------- */

  const handleSave = useCallback(
    async (content: string) => {
      if (!activeTab) return;
      await api.put('/workspace/file', { content }, {
        params: { path: activeTab.filePath },
      });
      // Update cache with new content
      const cached = contentCache.current[activeTab.filePath];
      if (cached) {
        cached.content = content;
        cached.size = new TextEncoder().encode(content).length;
      }
      markDirty(activeTab.id, false);
    },
    [activeTab, markDirty],
  );

  /* -------------------------------------------------------------- */
  /*  Close handler (called by tab bar and editor)                   */
  /* -------------------------------------------------------------- */

  const handleCloseTab = useCallback(
    (tabId: string) => {
      closeTab(tabId);
      // Remove cached content
      const tab = tabs.find((t) => t.id === tabId);
      if (tab) {
        delete contentCache.current[tab.filePath];
      }
      // If no tabs remain, close the viewer entirely
      if (tabs.length <= 1) {
        onClose();
      }
    },
    [closeTab, tabs, onClose],
  );

  /** Close the active tab (used by FileEditorCore's onClose). */
  const handleCloseActive = useCallback(() => {
    if (activeTab) {
      handleCloseTab(activeTab.id);
    } else {
      onClose();
    }
  }, [activeTab, handleCloseTab, onClose]);

  /* -------------------------------------------------------------- */
  /*  Content change tracking (marks tab dirty)                      */
  /* -------------------------------------------------------------- */

  const handleContentChange = useCallback(
    (content: string) => {
      if (!activeTab) return;
      const cached = contentCache.current[activeTab.filePath];
      const originalContent = cached?.content ?? '';
      markDirty(activeTab.id, content !== originalContent);
    },
    [activeTab, markDirty],
  );

  /* -------------------------------------------------------------- */
  /*  Status info callback for renderers                             */
  /* -------------------------------------------------------------- */

  /**
   * Adapts the per-renderer onStatusInfo shapes (dimensions, pageInfo,
   * rowColCount, customInfo) into the unified StatusBarInfo.extraInfo map.
   */
  const handleStatusInfo = useCallback(
    (info: { dimensions?: string; pageInfo?: string; rowColCount?: string; customInfo?: string }) => {
      const extraInfo: Record<string, string> = {};
      if (info.dimensions) extraInfo['Dimensions'] = info.dimensions;
      if (info.pageInfo) extraInfo['Page'] = info.pageInfo;
      if (info.rowColCount) extraInfo['Size'] = info.rowColCount;
      if (info.customInfo) extraInfo['Info'] = info.customInfo;
      setStatusBarInfo((prev) => ({
        ...prev,
        extraInfo: Object.keys(extraInfo).length > 0 ? extraInfo : prev.extraInfo,
      }));
    },
    [],
  );

  /* -------------------------------------------------------------- */
  /*  Render the appropriate component for the active tab            */
  /* -------------------------------------------------------------- */

  function renderActiveContent() {
    if (!activeTab) {
      return (
        <div className="flex-1 flex items-center justify-center text-[var(--color-text-secondary)] text-sm">
          No file open
        </div>
      );
    }

    if (loadingContent) {
      return <LoadingFallback />;
    }

    if (contentError) {
      return (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 text-[var(--color-text-secondary)]">
          <span className="material-symbols-outlined text-3xl text-red-400">error</span>
          <span className="text-sm">{contentError}</span>
        </div>
      );
    }

    const cached = contentCache.current[activeTab.filePath];
    if (!cached) return <LoadingFallback />;

    const { viewType, filePath, fileName, gitStatus } = activeTab;

    // --- Text / Markdown / SVG: delegate to existing FileEditorCore ---
    if (viewType === 'text' || viewType === 'markdown' || viewType === 'svg') {
      return (
        <FileEditorCore
          key={filePath}
          filePath={filePath}
          fileName={fileName}
          workspaceId={workspaceIdRef.current}
          initialContent={cached.content}
          committedContent={cached.committedContent}
          onSave={handleSave}
          onClose={handleCloseActive}
          gitStatus={gitStatus}
          onAttachToChat={onAttachToChat}
          readonly={cached.readonly}
          variant={variant}
          onToggleMode={onToggleMode}
          onSaveWithDiff={onSaveWithDiff}
          onContentChange={handleContentChange}
        />
      );
    }

    // --- Non-text types: lazy-loaded renderers ---
    const rendererProps = {
      filePath,
      fileName,
      content: cached.content,
      encoding: (cached.encoding ?? 'base64') as 'utf-8' | 'base64',
      mimeType: cached.mimeType ?? 'application/octet-stream',
      fileSize: cached.size,
      onStatusInfo: handleStatusInfo,
    };

    return (
      <Suspense fallback={<LoadingFallback />}>
        {viewType === 'image' && <ImageRenderer {...rendererProps} />}
        {viewType === 'pdf' && <PdfRenderer {...rendererProps} />}
        {viewType === 'html-preview' && <HtmlRenderer {...rendererProps} />}
        {viewType === 'video' && <VideoRenderer {...rendererProps} />}
        {viewType === 'audio' && <AudioRenderer {...rendererProps} />}
        {viewType === 'csv' && <CsvRenderer {...rendererProps} />}
        {viewType === 'unsupported' && (
          <UnsupportedRenderer
            {...rendererProps}
            onAttachToChat={onAttachToChat ? (path: string) => {
              onAttachToChat({
                id: path,
                name: fileName,
                type: 'file',
                path,
                workspaceId: workspaceIdRef.current,
                workspaceName: '',
              });
            } : undefined}
          />
        )}
      </Suspense>
    );
  }

  /* -------------------------------------------------------------- */
  /*  Layout                                                         */
  /* -------------------------------------------------------------- */

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg)] text-[var(--color-text)]">
      {/* Tab bar */}
      <FileViewerTabBar
        tabs={tabs}
        activeTabId={activeTab?.id ?? null}
        onSwitch={switchTab}
        onClose={handleCloseTab}
      />

      {/* Renderer area */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {renderActiveContent()}
      </div>

      {/* Status bar — shown when there is an active tab with loaded content */}
      {activeTab && !loadingContent && !contentError && (
        <FileViewerStatusBar
          fileName={activeTab.fileName}
          fileSize={statusBarInfo.fileSize}
          viewType={activeTab.viewType}
          encoding={statusBarInfo.encoding}
          extraInfo={statusBarInfo.extraInfo}
        />
      )}
    </div>
  );
}
